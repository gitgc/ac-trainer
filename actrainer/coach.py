"""The real-time coach: decides what to work on, what to say and when to say it.

Coaching loop, like a real driver coach:
  1. learn the track from your first clean lap (corners, braking points)
  2. after each lap compare every corner with your best version of it - and with the
     fastest rival in the same car, when the ACTrainerLink CSP app is recording - and find
     *why* time was lost (braking point, apex speed, throttle pickup, gear...)
  3. pick one or two consistent problems as the focus, cue them on the approach to that
     corner, and give a one-word verdict right after it
  4. when there's nothing obvious left, push the braking points a little later

Everything that makes up the optimum lap (your best corner by corner, the rivals' best) is
kept in a lap library on disk, so the next session starts where this one left off.
"""
from __future__ import annotations

import math
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import phrases, report
from .analysis import (Capability, CornerBest, CornerResult, Issue, RivalBest, analyze_corners,
                       diagnose, extract)
from .config import Config
from .gears import ShiftAdvice, analyze_shifts
from .laps import Lap, LapRecorder, Trace, build_trace
from .overlay import OverlayWriter
from .rivals import RivalLap
from .sample import Sample, SessionInfo
from .speech import CUE, FEEDBACK, INFO
from .storage import Store
from .track import Corner, TrackModel, detect_corners

FREEZE_AFTER = 3   # clean laps used to settle the corner map, then it stays fixed
KEEP_RIVALS = 10   # rival laps kept in memory


@dataclass
class Focus:
    corner: int
    kind: str
    amount: float
    gear: int = 0
    since_lap: int = 0
    source: str = "self"             # self = vs your own best, rival = vs a faster driver
    base_bp: float | None = None     # exploration: braking point / time / min speed before
    base_time: float = 0.0
    base_vmin: float = 0.0


@dataclass
class Live:
    d: float = 0.0
    speed: float = 0.0
    brake: float = 0.0
    g_lat: float = 0.0
    in_pit: bool = True
    t_to_brake: float = math.inf


@dataclass
class TargetLap:
    """One of your laps that can hold corner bests (kept as raw columns so it can be saved)."""
    lap_id: str
    trace: Trace
    cols: dict[str, np.ndarray]
    time: float


@dataclass
class RivalEntry:
    lap: RivalLap
    trace: Trace
    lap_id: str


def crossed(a: float, b: float, x: float) -> bool:
    """Did the car pass distance x moving from a to b (b < a = crossed the line)?"""
    if b >= a:
        return a < x <= b
    return x > a or x <= b


def _compact(cols: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {k: v.astype(np.float32) for k, v in cols.items()}


class Coach:
    def __init__(self, info: SessionInfo, cfg: Config, speech, store: Store,
                 tip_listener: Callable | None = None, log: Callable[[str], None] = print,
                 overlay: OverlayWriter | None = None):
        self.info, self.cfg, self.speech, self.store, self.log = info, cfg, speech, store, log
        self.tip_listener = tip_listener
        self.overlay = overlay
        self.session_id = time.strftime("%Y%m%d%H%M%S")
        self.names = cfg.corner_names.get(info.track_id) or cfg.corner_names.get(info.track) or {}
        self.meta = store.load_meta()
        self.L = info.track_length or self.meta.get("track_length", 0.0)
        self.rpm_limit = info.rpm_limit or self.meta.get("rpm_limit", 0.0)
        self.model: TrackModel | None = store.load_model()
        self.ref_time: float | None = None
        self.ref_trace: Trace | None = None
        self.target_laps: list[TargetLap] = []
        self.rival_laps: list[RivalEntry] = []
        self._saved: set[str] = set()
        self._load_library()
        if self.model and abs(self.model.length - self.L) > 5:
            self.log("[coach] track length changed since last time - relearning the track")
            self.model, self.ref_time, self.ref_trace = None, None, None
            self.target_laps, self.rival_laps = [], []
        if self.model:
            self.model.rename(self.names)
            self._index_markers()

        self.best: dict[int, CornerBest] = {}
        self.rival_best: dict[int, RivalBest] = {}
        self.history: dict[int, deque[CornerResult]] = defaultdict(lambda: deque(maxlen=5))
        self.focus: dict[int, Focus] = {}
        self.caps: dict[int, Capability] = {}
        self.explored: dict[int, int] = {}
        self.gave_up: dict[int, int] = {}
        self.shift_laps: deque[dict[str, np.ndarray]] = deque(maxlen=8)
        self.shift_said: dict[tuple[int, str], int] = {}
        self.lap_no = 0
        self.session_best: float | None = None
        self.lap_times: list[tuple[int, float, bool]] = []
        self.recorder = LapRecorder(self._on_lap, cfg.telemetry.max_tyres_out)
        self.live = Live()
        self._prev_d: float | None = None
        self._lap_obj: Lap | None = None
        self._fired: set[str] = set()
        self._was_in_pit = True
        self._pending_rivals: list[RivalLap] = []
        self._rival_news: RivalEntry | None = None
        self._optimum_said: tuple[int, float] | None = None
        self._still: dict[int, int] = defaultdict(int)   # same "still..." verdicts in a row
        speech.gate = self._gate
        if self.model:
            self._recompute_bests()

    def _load_library(self) -> None:
        if not self.L:
            return
        for name, cols, lap_time, meta in self.store.load_laps():
            tr = build_trace(cols, self.L, lap_time)
            if tr is None:
                continue
            self._saved.add(name)
            if meta.get("kind") == "rival":
                lap = RivalLap(meta.get("driver", ""), meta.get("model", ""), lap_time, cols,
                               bool(meta.get("pedals", True)))
                self.rival_laps.append(RivalEntry(lap, tr, name))
            else:
                self.target_laps.append(TargetLap(name, tr, cols, lap_time))
        self.rival_laps.sort(key=lambda e: e.lap.lap_time)
        ref = self.store.load_reference()
        if ref:
            cols, lap_time = ref
            self.ref_trace = build_trace(cols, self.L, lap_time)
            if self.ref_trace is not None:
                self.ref_time = lap_time
                if not any(abs(t.time - lap_time) < 1e-3 for t in self.target_laps):
                    self.target_laps.append(TargetLap(f"own_pb_{round(lap_time * 1000)}",
                                                      self.ref_trace, _compact(cols), lap_time))

    # ---------------------------------------------------------------------------------
    def start(self) -> None:
        car = self.info.car.replace("_", " ")
        self.log(f"[coach] {self.info.car} @ {self.info.track_id}  "
                 f"({self.L:.0f} m, rev limit {self.rpm_limit:.0f})")
        if self.model and self.ref_time:
            self.log(f"[coach] {len(self.model.corners)} corners mapped, best lap "
                     f"{report.fmt_time(self.ref_time)}, optimum {report.fmt_time(self.optimum_lap)}"
                     f" ({len(self.target_laps)} own / {len(self.rival_laps)} rival laps in the library)")
            text = f"Coach ready. Your best here is {phrases.lap_time(self.ref_time)}."
            if self.optimum_lap and self.optimum_lap < self.ref_time - 0.1:
                text += f" Optimum lap {phrases.lap_time(self.optimum_lap)}."
            self.speech.say(text, INFO, key="hello", ttl=30)
            self._update_overlay()
        else:
            self.speech.say(f"Coach ready. New track for the {car}. Drive a clean lap so I can learn it.",
                            INFO, key="hello", ttl=30)

    def close(self) -> None:
        self._save_library()
        if self.lap_times:
            self.log(report.session_summary(self))

    # -- live ---------------------------------------------------------------------------
    def on_sample(self, s: Sample) -> None:
        self.recorder.feed(s)          # may complete a lap -> _on_lap
        if not self.L:
            return
        d = s.pos * self.L
        self.live = Live(d, s.speed, s.brake, s.g_lat, bool(s.in_pit), self._time_to_brake(d, s.speed))
        lap = self.recorder.current
        if lap is not self._lap_obj:
            self._lap_obj = lap
            self._fired = set()
        prev, self._prev_d = self._prev_d, d
        if s.in_pit and not self._was_in_pit and self.lap_no >= 2:
            self._pit_summary()
        self._was_in_pit = bool(s.in_pit)
        if self.model is None or prev is None or s.in_pit:
            return
        jump = abs(d - prev)
        if 100 < jump < self.L / 2:   # teleported (reset / back to pits)
            return
        for c in self.model.corners:
            k = c.index
            f = self.focus.get(k)
            if f and f"cue{k}" not in self._fired and crossed(prev, d, self._cue_point(c)):
                self._fired.add(f"cue{k}")
                self._cue(c, f)
            if crossed(prev, d, c.marker - 10):
                self.speech.cancel(f"cue{k}")
            if (f"done{k}" not in self._fired and lap is not None and lap.at_line
                    and crossed(prev, d, c.done)):
                self._fired.add(f"done{k}")
                self._corner_done(c, lap)

    def _index_markers(self) -> None:
        corners = self.model.corners
        self._markers = np.array(sorted(c.marker for c in corners)) if corners else np.array([])
        self._busy = [(c.marker - 10, c.done) for c in corners]   # braking zone -> corner exit

    def _time_to_brake(self, d: float, speed: float) -> float:
        if self.model is None or not len(self._markers):
            return math.inf
        ahead = (self._markers - d) % self.L
        ahead = ahead[ahead > 5]
        if not len(ahead):
            return math.inf
        return float(ahead.min()) / max(speed / 3.6, 5.0)

    def _gate(self, duration: float, priority: int) -> bool:
        lv = self.live
        if lv.in_pit or lv.speed < 25:
            return True
        if lv.brake > 0.15 or abs(lv.g_lat) > (1.2 if priority >= CUE else 0.9):
            return False
        if self.model is None:
            return True
        if priority < CUE and any(a <= lv.d <= b for a, b in self._busy):
            return False
        need = duration * 0.6 if priority >= CUE else duration + 0.4
        return lv.t_to_brake > need

    def _cue_text(self, c: Corner, f: Focus) -> str:
        if f.kind == "explore":
            return phrases.cue("explore", c.name)
        return phrases.cue(f.kind, c.name, f.amount, f.gear or int(f.amount))

    def _cue_point(self, c: Corner) -> float:
        corners = self.model.corners
        i = corners.index(c)
        prev = corners[i - 1]
        lower = prev.done if i > 0 else prev.done - self.L
        f = self.focus[c.index]
        lead = self.cfg.coach.lead_time + self.speech.duration(self._cue_text(c, f))
        p = max(c.marker - c.v_max / 3.6 * lead, lower + 5)
        return p % self.L

    def _cue(self, c: Corner, f: Focus) -> None:
        self.speech.say(self._cue_text(c, f), CUE, key=f"cue{c.index}", ttl=15)
        if self.tip_listener:
            self.tip_listener(c.apex, f.kind, None)

    def _corner_done(self, c: Corner, lap: Lap) -> None:
        f = self.focus.get(c.index)
        verbosity = self.cfg.coach.verbosity
        if not self.cfg.coach.corner_feedback or verbosity == "quiet" and f is None:
            return
        best = self.best.get(c.index)
        if best is None:
            return
        # only resample the part of the lap around this corner (samples right after the line
        # may still read ~L, so those just keep the scan going back to the lap start)
        start = c.start - 60
        k0 = len(lap.samples) - 1
        while k0 > 0 and lap.samples[k0].pos * self.L > start:
            k0 -= 1
        tr = build_trace(lap.columns(k0), self.L, None, at_line=k0 == 0)
        if tr is None:
            return
        m = extract(tr, c, self.rpm_limit, upto=c.done)
        tp = extract(best.trace, c, self.rpm_limit, upto=c.done)
        if m is None or tp is None:
            return
        loss = m.time - tp.time
        text = None
        if f is not None and f.kind == "explore":
            gain = m.time - f.base_time
            if m.brake_point is None or f.base_bp is None or m.brake_point - f.base_bp < 2:
                text = None   # didn't try it this time
            elif m.v_min < f.base_vmin - 3 or m.lockup > 5 or gain > 0.05:
                text = "Too deep. Back to your old marker."
                self.focus.pop(c.index, None)
                self.explored[c.index] = self.lap_no
                self._update_overlay()
            elif gain < -0.02:
                text = f"Yes! {phrases.amount(gain)} quicker. Keep that marker."
                f.base_bp, f.base_time, f.base_vmin, f.since_lap = m.brake_point, m.time, m.v_min, self.lap_no
                self._update_overlay()
            else:
                text = "Same time. Make it stick."
        elif f is not None and f.source == "rival" and c.index in self.rival_best:
            rp = extract(self.rival_best[c.index].trace, c, self.rpm_limit, upto=c.done)
            if rp is not None:
                issues = diagnose(m, rp, c, relative_only=True)
                if any(i.kind == f.kind for i in issues):
                    text = phrases.still(f.kind)
                elif m.time - rp.time <= 0.02:
                    text = "That's rival pace!"
                else:
                    text = "Better"
        elif f is not None:
            issues = diagnose(m, tp, c, self.caps.get(c.index))
            if any(i.kind == f.kind for i in issues):
                text = phrases.still(f.kind)
            elif loss < -0.03:
                text = f"Great, {phrases.amount(loss)} quicker"
            else:
                text = "Better"
        elif loss > (0.15 if verbosity == "chatty" else 0.3):
            text = f"{c.name}, lost {phrases.amount(loss)}"
        elif verbosity == "chatty" and loss < -0.1:
            text = f"{c.name}, {phrases.amount(loss)} quicker"
        if text and f is not None:
            repeat = text == phrases.still(f.kind)
            self._still[c.index] = self._still[c.index] + 1 if repeat else 0
            if self._still[c.index] > 2:
                return   # they know; the cue before the corner keeps reminding them
        if text:
            self.speech.say(text, FEEDBACK, key=f"fb{c.index}", ttl=4)

    # -- rivals -------------------------------------------------------------------------
    def add_rival_lap(self, lap: RivalLap) -> None:
        """A lap by another driver (from the ACTrainerLink CSP app)."""
        if not self.L:
            self._pending_rivals.append(lap)
            return
        tr = build_trace(lap.cols, self.L, lap.lap_time)
        if tr is None or tr.end < self.L - 5:
            return
        if self.ref_time and lap.lap_time < 0.85 * self.ref_time:
            self.log(f"[rivals] ignoring {lap.label} {report.fmt_time(lap.lap_time)} - implausibly fast")
            return
        lap_id = "rival_" + re.sub(r"[^A-Za-z0-9]+", "", lap.driver)[:20] + f"_{round(lap.lap_time * 1000)}"
        if any(e.lap_id == lap_id for e in self.rival_laps):
            return
        entry = RivalEntry(lap, tr, lap_id)
        fastest = not self.rival_laps or lap.lap_time < self.rival_laps[0].lap.lap_time
        self.rival_laps.append(entry)
        self.rival_laps.sort(key=lambda e: e.lap.lap_time)
        del self.rival_laps[KEEP_RIVALS:]
        if entry not in self.rival_laps:
            return
        self.log(f"[rivals] {lap.label}: {report.fmt_time(lap.lap_time)}"
                 + ("" if lap.pedals else " (speed trace only, no pedal data)"))
        if fastest:
            self._rival_news = entry
        if self.model and self._update_rival_bests(entry):
            self._save_library()

    def _update_rival_bests(self, entry: RivalEntry) -> bool:
        changed = False
        for c in self.model.corners:
            m = extract(entry.trace, c, self.rpm_limit)
            if m is None:
                continue
            b = self.rival_best.get(c.index)
            if b is None or m.time < b.time:
                self.rival_best[c.index] = RivalBest(m.time, m, entry.trace, entry.lap_id, entry.lap.label)
                changed = True
        return changed

    # -- end of lap ---------------------------------------------------------------------
    def _on_lap(self, lap: Lap) -> None:
        if not lap.complete:
            return
        if not self.L and not self._estimate_length(lap):
            return
        cols = lap.columns()
        tr = build_trace(cols, self.L, lap.time)
        if tr is None:
            return
        self.lap_no += 1
        no = self.lap_no
        valid = lap.valid
        if not self.info.rpm_limit:
            self.rpm_limit = max(self.rpm_limit, float(cols["rpm"].max()))
        self.lap_times.append((no, lap.time, valid))
        if not cols["in_pit"].any():
            self.shift_laps.append(cols)

        prev_ref = self.ref_time
        is_pb = valid and (prev_ref is None or lap.time < prev_ref)
        if is_pb:
            self.ref_time, self.ref_trace = lap.time, tr
            self.store.save_reference(cols, lap.time)
        if valid and (self.session_best is None or lap.time < self.session_best):
            self.session_best = lap.time

        first_model = self.model is None
        if valid and (self.model is None or not self.model.frozen):
            used = (self.model.laps_used if self.model else 0) + 1
            self.model = TrackModel(self.L, detect_corners(self.ref_trace, self.names),
                                    frozen=used >= FREEZE_AFTER, laps_used=used)
            self.store.save_model(self.model)
            self._index_markers()
            self.history.clear()
            self.focus.clear()
            self._recompute_bests()
        for pending in self._pending_rivals:
            self.add_rival_lap(pending)
        self._pending_rivals.clear()

        results: list[CornerResult] = []
        improved = False
        if self.model:
            results = analyze_corners(tr, self.model, self.best, self.rpm_limit)
            for r in results:
                rb = self.rival_best.get(r.corner.index)
                if rb is not None and r.metrics.time - rb.time > 0.03:
                    r.rival, r.rival_gap = rb, r.metrics.time - rb.time
                    r.rival_issues = diagnose(r.metrics, rb.metrics, r.corner, relative_only=True)
            if valid:
                target = TargetLap(f"own_{self.session_id}_{no}", tr, _compact(cols), lap.time)
                self.target_laps.append(target)
                for r in results:
                    b = self.best.get(r.corner.index)
                    if b is None or r.metrics.time < b.time:
                        self.best[r.corner.index] = CornerBest(r.metrics.time, r.metrics, tr, target.lap_id)
                        improved = True
                    self.history[r.corner.index].append(r)
        shifts = analyze_shifts(list(self.shift_laps), self.rpm_limit) if len(self.shift_laps) >= 2 else []
        if self.model and valid:
            self.caps = {r.corner.index: r.cap for r in results if r.cap}
            self._choose_focus(results, tr)
        if improved:
            self._save_library()
        self._update_overlay()

        self.log(report.lap_report(self, lap, no, results, shifts, prev_ref))
        if self.cfg.coach.lap_summary:
            self._summary(lap, results, shifts, prev_ref, is_pb, first_model and self.model is not None)

    def _update_overlay(self) -> None:
        if self.overlay is not None:
            self.overlay.write(self)

    def _estimate_length(self, lap: Lap) -> bool:
        t = np.array([s.t for s in lap.samples])
        v = np.array([s.speed for s in lap.samples]) / 3.6
        length = float(np.sum(v[1:] * np.diff(t)))
        if length < 500:
            return False
        self.L = length
        self.meta["track_length"] = length
        self.store.save_meta(self.meta)
        self.log(f"[coach] track length estimated at {length:.0f} m")
        return True

    def _recompute_bests(self) -> None:
        self.best = {}
        for tl in self.target_laps:
            for c in self.model.corners:
                m = extract(tl.trace, c, self.rpm_limit)
                if m and (c.index not in self.best or m.time < self.best[c.index].time):
                    self.best[c.index] = CornerBest(m.time, m, tl.trace, tl.lap_id)
        self.rival_best = {}
        for entry in self.rival_laps:
            self._update_rival_bests(entry)

    def _save_library(self) -> None:
        """Keep every lap that holds a corner best (yours or a rival's) + the fastest rival lap."""
        if not self.model:
            return
        own = {t.lap_id: t for t in self.target_laps}
        rivals = {e.lap_id: e for e in self.rival_laps}
        keep: dict[str, tuple[dict, float, dict]] = {}
        for b in self.best.values():
            t = own.get(b.lap_id)
            if t is not None:
                keep[b.lap_id] = (t.cols, t.time, {"kind": "own"})
        holders = {b.lap_id for b in self.rival_best.values()} | ({self.rival_laps[0].lap_id} if self.rival_laps else set())
        for lap_id in holders:
            e = rivals.get(lap_id)
            if e is not None:
                keep[lap_id] = (e.lap.cols, e.lap.lap_time, {"kind": "rival", "driver": e.lap.driver,
                                                              "model": e.lap.model, "pedals": e.lap.pedals})
        for lap_id, (cols, lap_time, meta) in keep.items():
            if lap_id not in self._saved:
                self.store.save_lap(lap_id, _compact(cols), lap_time, meta)
                self._saved.add(lap_id)
        self.store.prune_laps(set(keep))
        self._saved &= set(keep)
        self.store.save_optimum(report.optimum_summary(self))

    @property
    def ideal_lap(self) -> float | None:
        """Your best time through every corner, added up."""
        if not self.model or not self.best or len(self.best) != len(self.model.corners):
            return None
        return sum(b.time for b in self.best.values())

    @property
    def optimum_lap(self) -> float | None:
        """Per corner, the better of your best and the fastest rival's."""
        if self.ideal_lap is None:
            return None
        return sum(min(b.time, self.rival_best[k].time) if k in self.rival_best else b.time
                   for k, b in self.best.items())

    def _choose_focus(self, results: list[CornerResult], tr: Trace) -> None:
        no = self.lap_no
        cands: dict[int, tuple[float, Issue, str]] = {}
        for r in results:
            k = r.corner.index
            hist = list(self.history[k])[-3:]
            prim = r.primary
            if prim is not None:
                kinds = [h.primary.kind for h in hist if h.primary]
                if not (len(hist) >= 2 and kinds.count(prim.kind) < 2):  # seen it twice?
                    avg_loss = sum(max(h.loss, 0.0) for h in hist) / max(len(hist), 1)
                    score = avg_loss + prim.score
                    if score >= 0.08:
                        cands[k] = (score, prim, "self")
            rp = r.rival_issues[0] if r.rival_issues else None
            if rp is not None and r.rival_gap >= 0.08:
                kinds = [h.rival_issues[0].kind for h in hist if h.rival_issues]
                if not (len(hist) >= 2 and kinds.count(rp.kind) < 2):
                    score = 0.7 * sum(h.rival_gap for h in hist) / max(len(hist), 1) + rp.score
                    own = cands.get(k)
                    if own is not None:   # your own data names the cause more precisely
                        cands[k] = (max(score, own[0]), own[1], "self")
                    elif score >= 0.1:
                        cands[k] = (score, rp, "rival")

        for k, f in list(self.focus.items()):
            if f.kind == "explore":
                if no - f.since_lap >= 2:
                    self.focus.pop(k)
                    self.explored[k] = no
                continue
            cand = cands.get(k)
            if no - f.since_lap >= 5:
                self.gave_up[k] = no      # rotate: give it a rest before nagging again
                self.focus.pop(k)
            elif cand is None or cand[1].kind != f.kind:
                self.focus.pop(k)
        for k, (score, issue, source) in sorted(cands.items(), key=lambda kv: -kv[1][0]):
            if k in self.focus:
                self.focus[k].amount = issue.amount
                continue
            if len(self.focus) >= self.cfg.coach.max_focus:
                break
            if no - self.gave_up.get(k, -99) < 4:
                continue
            self.focus[k] = Focus(k, issue.kind, issue.amount, gear=int(issue.amount)
                                  if issue.kind.startswith("gear") else 0, since_lap=no, source=source)

        if not self.focus and self.cfg.coach.explore_brake_points and no >= 3:
            options = []
            for c in self.model.corners:
                hist = list(self.history[c.index])[-2:]
                if (not c.heavy or len(hist) < 2 or no - self.explored.get(c.index, -99) < 5
                        or any(h.metrics.brake_point is None or h.metrics.lockup > 3 for h in hist)):
                    continue
                if abs(hist[0].metrics.brake_point - hist[1].metrics.brake_point) < 6:
                    options.append((c.v_max - c.v_min, c, hist[-1]))
            if options:
                _, c, last = max(options, key=lambda o: o[0])
                base = extract(tr, c, self.rpm_limit, upto=c.done)
                if base is not None:
                    self.focus[c.index] = Focus(c.index, "explore", 5.0, since_lap=no,
                                                base_bp=base.brake_point, base_time=base.time,
                                                base_vmin=base.v_min)

    def _summary(self, lap: Lap, results: list[CornerResult], shifts: list[ShiftAdvice],
                 prev_ref: float | None, is_pb: bool, learned: bool) -> None:
        say = self.speech.say
        t = phrases.lap_time(lap.time)
        if not lap.valid:
            why = ", track limits" if lap.invalid_reason == "track limits" else ""
            say(f"{t}, lap invalid{why}.", INFO, key="lap", ttl=20)
        elif is_pb and prev_ref is not None:
            say(f"New personal best! {t}, {phrases.amount(prev_ref - lap.time)} quicker.", INFO, key="lap", ttl=20)
        elif is_pb:
            say(f"{t}. That's the reference lap.", INFO, key="lap", ttl=20)
        else:
            say(f"{t}, {phrases.delta(lap.time - self.ref_time)}.", INFO, key="lap", ttl=20)
        if learned:
            say(f"Track mapped, {len(self.model.corners)} corners. Let's find some time.", INFO,
                key="mapped", ttl=30)

        if self._rival_news is not None:
            e, self._rival_news = self._rival_news, None
            say(f"Fastest rival so far, {phrases.speakable_name(e.lap.driver)}, "
                f"{phrases.lap_time(e.lap.lap_time)}.", INFO, key="rival", ttl=30)

        by_corner = {r.corner.index: r for r in results}

        def weight(f: Focus) -> float:
            r = by_corner.get(f.corner)
            return 0.0 if r is None else max(r.rival_gap, r.loss)

        for f in sorted(self.focus.values(), key=lambda f: -weight(f))[:1]:
            c = next(c for c in self.model.corners if c.index == f.corner)
            r = by_corner.get(f.corner)
            if f.kind == "explore":
                say(f"Let's find time at {c.name}. Try braking 5 meters later.", INFO, key="why", ttl=30)
            elif f.source == "rival" and r is not None and r.rival is not None:
                who = phrases.speakable_name(r.rival.driver)
                say(f"{c.name}: {who} is {phrases.amount(r.rival_gap)} quicker, "
                    f"{phrases.rival_reason(f.kind, f.amount, f.gear)}.", INFO, key="why", ttl=30)
            elif r is not None and r.rival is not None and r.rival_gap >= max(r.loss, 0.1):
                who = phrases.speakable_name(r.rival.driver)
                say(f"{c.name}: {who} is {phrases.amount(r.rival_gap)} quicker. You're "
                    f"{phrases.REASON[f.kind]}.", INFO, key="why", ttl=30)
            elif r is not None and r.loss >= 0.1:
                say(f"{c.name} cost {phrases.amount(r.loss)}, {phrases.REASON[f.kind]}.", INFO, key="why", ttl=30)
            else:
                say(f"Work on {c.name}: {phrases.REASON[f.kind]}.", INFO, key="why", ttl=30)

        for s in shifts[:1]:
            said = self.shift_said.get((s.gear, s.kind), -99)
            if self.lap_no - said < 3:
                continue
            self.shift_said[(s.gear, s.kind)] = self.lap_no
            g = phrases.gear(s.gear)
            if s.kind == "limiter":
                text = f"You're hitting the limiter in {g}. Shift up a bit earlier."
            else:
                verb = "try" if s.guess else "shift near"
                lead = f"Hold {g} gear longer" if s.kind == "early" else f"Shift out of {g} earlier"
                text = f"{lead}, {verb} {phrases.rpm(s.best_rpm)}."
            say(text, INFO, key="shift", ttl=40)
            if self.tip_listener:
                self.tip_listener(None, f"shift_{s.kind}", (s.gear, s.best_rpm))

        opt = self.optimum_lap
        if self.rival_best and opt and self.ref_time and opt < self.ref_time - 0.1:
            last = self._optimum_said
            if last is None or self.lap_no - last[0] >= 4 or last[1] - opt > 0.1:
                self._optimum_said = (self.lap_no, opt)
                say(f"Optimum lap, with the rivals' best corners, {phrases.lap_time(opt)}.", INFO,
                    key="optimum", ttl=30)
        elif self.cfg.coach.verbosity == "chatty" and self.ideal_lap:
            say(f"Ideal lap {phrases.lap_time(self.ideal_lap)}.", INFO, key="ideal", ttl=30)

    def _pit_summary(self) -> None:
        if self.session_best is None:
            return
        text = f"In the pits. Session best {phrases.lap_time(self.session_best)}"
        best = self.optimum_lap or self.ideal_lap
        if best:
            text += f", optimum {phrases.lap_time(best)}"
        self.speech.say(text + ".", INFO, key="pit", ttl=30)
        self._save_library()

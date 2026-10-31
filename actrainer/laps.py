"""Splitting the sample stream into laps, and resampling laps onto a distance grid."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .sample import FIELDS, Sample

GRID_STEP = 2.0  # metres between points of a resampled lap


@dataclass
class Lap:
    number: int
    samples: list[Sample] = field(default_factory=list)
    at_line: bool = False        # recording started at the start/finish line
    time: float = 0.0
    invalid_reason: str = ""

    @property
    def complete(self) -> bool:
        return self.at_line and self.time > 0

    @property
    def valid(self) -> bool:
        return self.complete and not self.invalid_reason

    def invalidate(self, reason: str) -> None:
        if not self.invalid_reason:
            self.invalid_reason = reason

    def columns(self, start: int = 0) -> dict[str, np.ndarray]:
        return samples_to_columns(self.samples[start:])


def samples_to_columns(samples: list[Sample]) -> dict[str, np.ndarray]:
    return {name: np.array([getattr(s, name) for s in samples], dtype=float) for name in FIELDS}


class LapRecorder:
    """Feeds on samples, calls on_lap(lap) every time a lap ends."""

    def __init__(self, on_lap: Callable[[Lap], None], max_tyres_out: int = 2):
        self.on_lap = on_lap
        self.max_tyres_out = max_tyres_out
        self.current: Lap | None = None
        self.pos_offset = 0.0   # spline origin vs timing line, learnt at the first crossing
        self._offset_learnt = False
        self._ended: Lap | None = None   # timer reset at the line, lap count not updated yet

    def feed(self, s: Sample) -> None:
        s.pos = (s.pos - self.pos_offset) % 1.0
        cur = self.current
        if cur is None or not cur.samples:
            self._start(s, at_line=s.lap_time < 1.0 and (s.pos < 0.02 or s.pos > 0.98))
            return
        prev = cur.samples[-1]
        if self._ended is not None and s.lap_time > 2.0:
            self._ended = None           # the lap was never counted: it was a restart
        if s.laps != prev.laps:
            if s.laps == prev.laps + 1 and self._ended is not None:
                ended, self._ended = self._ended, None
                if not self._offset_learnt:
                    self._learn_offset(s, ended)
                ended.time = s.last_lap if s.last_lap > 0 else ended.samples[-1].lap_time
                cur.number = s.laps + 1
                cur.samples.append(s)
                self._finish(ended)
                return
            if s.laps == prev.laps + 1:
                if not self._offset_learnt:
                    self._learn_offset(s, cur)
                cur.time = s.last_lap if s.last_lap > 0 else prev.lap_time
                self._finish(cur)
                self._start(s, at_line=True)
            else:  # session restarted
                self._start(s, at_line=s.lap_time < 1.0)
            return
        if s.lap_time + 0.5 < prev.lap_time:  # timer reset without a lap (restart, back to pits)
            # ...or AC resetting the timer a frame before it counts the lap: keep the lap that
            # just ended until the lap count catches up
            at_line = s.lap_time < 1.0 and (prev.pos > 0.9 or prev.pos < 0.02)
            self._ended = cur if at_line and cur.at_line else None
            self._start(s, at_line=s.lap_time < 1.0)
            return
        dp = s.pos - prev.pos
        if prev.pos > 0.9 and s.pos < 0.1 and not cur.at_line and s.lap_time < 2.0:
            # hotlap mode: timing starts at the first crossing without a lap being counted
            self._start(s, at_line=True)
            return
        if abs(dp) < 0.5 and abs(dp) > 0.05:
            cur.invalidate("teleported")
        if s.in_pit:
            cur.invalidate("pit lane")
        if s.tyres_out > self.max_tyres_out:
            cur.invalidate("track limits")
        cur.samples.append(s)

    def _learn_offset(self, s: Sample, lap: Lap) -> None:
        self._offset_learnt = True
        p = s.pos
        if 0.02 < p < 0.98:
            # the AI spline doesn't start at the timing line on this track: rotate positions
            self.pos_offset = (self.pos_offset + p) % 1.0
            s.pos = 0.0
            lap.invalidate("spline offset learnt")

    def _start(self, s: Sample, at_line: bool) -> None:
        self.current = Lap(number=s.laps + 1, samples=[s], at_line=at_line)
        if s.in_pit:
            self.current.invalidate("pit lane")

    def _finish(self, lap: Lap) -> None:
        if lap.complete:
            p = np.array([x.pos for x in lap.samples])
            if p.max() < 0.95 or len(p) < 20:
                lap.invalidate("incomplete")
        self.on_lap(lap)


class Trace:
    """A lap (or part of one) resampled every GRID_STEP metres of track distance."""

    CHANNELS = ("t", "speed", "gas", "brake", "rpm", "steer", "g_lat", "slip_f", "slip_r", "x", "z")

    def __init__(self, step: float, d: np.ndarray, data: dict[str, np.ndarray], gear: np.ndarray):
        self.step = step
        self.d = d
        self.gear = gear
        for name in self.CHANNELS:
            setattr(self, name, data[name])

    def __len__(self) -> int:
        return len(self.d)

    @property
    def start(self) -> float:
        return float(self.d[0])

    @property
    def end(self) -> float:
        return float(self.d[-1])

    def covers(self, a: float, b: float) -> bool:
        return self.start <= a + self.step and self.end >= b - self.step

    def idx(self, dist: float) -> int:
        return int(min(max(round((dist - self.d[0]) / self.step), 0), len(self.d) - 1))

    def time_at(self, dist: float) -> float:
        return float(np.interp(dist, self.d, self.t))


def build_trace(cols: dict[str, np.ndarray], track_len: float, lap_time: float | None = None,
                at_line: bool = True, step: float = GRID_STEP) -> Trace | None:
    """Resample raw samples onto a distance grid.

    lap_time given = a finished lap (grid covers the whole lap and ends at lap_time);
    otherwise the grid stops at the furthest point reached (live, in-progress lap).
    """
    pos = cols["pos"].astype(float).copy()
    n = len(pos)
    if n < 3:
        return None
    k = max(1, n // 10)
    if at_line:  # first samples of a lap can still read ~0.999 before the spline wraps
        head = pos[:k]
        head[head > 0.9] -= 1.0
    if lap_time is not None:  # ...and the last ones can already read ~0.001
        tail = pos[-k:]
        tail[tail < 0.1] += 1.0
    d = pos * track_len
    t = cols["lap_time"].astype(float).copy()
    data = {
        "speed": cols["speed"], "gas": cols["gas"], "brake": cols["brake"], "rpm": cols["rpm"],
        "steer": cols["steer"], "g_lat": cols["g_lat"], "x": cols["x"], "z": cols["z"],
        "slip_f": np.minimum(cols["slip_fl"], cols["slip_fr"]),
        "slip_r": np.maximum(cols["slip_rl"], cols["slip_rr"]),
    }
    gear = cols["gear"].astype(float)

    if at_line and d[0] > 0:
        d = np.concatenate([[0.0], d])
        t = np.concatenate([[0.0], t])
        data = {key: np.concatenate([[v[0]], v]) for key, v in data.items()}
        gear = np.concatenate([[gear[0]], gear])
    if lap_time is not None and d[-1] < track_len:
        d = np.concatenate([d, [track_len]])
        t = np.concatenate([t, [lap_time]])
        data = {key: np.concatenate([v, [v[-1]]]) for key, v in data.items()}
        gear = np.concatenate([gear, [gear[-1]]])

    # keep strictly increasing distance (drops samples while stopped or rolling back)
    prev_max = np.maximum.accumulate(np.concatenate([[-np.inf], d[:-1]]))
    keep = d > prev_max
    d, t, gear = d[keep], t[keep], gear[keep]
    data = {key: v[keep] for key, v in data.items()}
    if len(d) < 3:
        return None

    lo = max(0.0, d[0])
    end = track_len if lap_time is not None else d[-1]
    grid = np.arange(np.ceil(lo / step) * step, end + 1e-6, step)
    out = {key: np.interp(grid, d, v) for key, v in data.items()}
    out["t"] = np.interp(grid, d, t)
    gi = np.clip(np.searchsorted(d, grid, side="right") - 1, 0, len(d) - 1)
    return Trace(step, grid, out, gear[gi].astype(int))

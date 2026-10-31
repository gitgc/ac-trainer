"""Console reports: per lap tables and the session summary."""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from . import phrases
from .analysis import CornerResult
from .gears import ShiftAdvice

if TYPE_CHECKING:
    from .coach import Coach
    from .laps import Lap


def fmt_time(t: float | None) -> str:
    if t is None:
        return "-:--.---"
    m, s = divmod(t, 60)
    return f"{int(m)}:{s:06.3f}"


def _num(x: float | None, fmt: str = "+.0f") -> str:
    return "" if x is None else format(x, fmt)


def lap_report(coach: "Coach", lap: "Lap", no: int, results: list[CornerResult],
               shifts: list[ShiftAdvice], prev_ref: float | None) -> str:
    status = "valid" if lap.valid else f"INVALID ({lap.invalid_reason})"
    head = f"\n=== Lap {no}  {fmt_time(lap.time)}  {status}"
    if prev_ref is not None:
        head += f"  {lap.time - prev_ref:+.3f} vs best {fmt_time(prev_ref)}"
    ideal = coach.ideal_lap
    if ideal:
        head += f"  | ideal {fmt_time(ideal)}"
    opt = coach.optimum_lap
    if opt and coach.rival_best and (ideal is None or opt < ideal - 0.0005):
        head += f"  optimum {fmt_time(opt)}"
    lines = [head]
    rivals = any(r.rival is not None for r in results)
    if results:
        lines.append(f"  {'corner':<14}{'loss':>7}{'brake':>7}{'vmin':>7}{'exit':>7}{'gear':>6}"
                     + (f"{'rival':>7}" if rivals else "") + "   main issue")
        for r in results:
            m, t = r.metrics, r.target
            dbp = m.brake_point - t.brake_point if m.brake_point is not None and t.brake_point is not None else None
            prim = r.primary
            note = ""
            if prim:
                note = phrases.REASON.get(prim.kind, prim.kind)
                if len(r.issues) > 1:
                    note += ", " + ", ".join(phrases.REASON.get(i.kind, i.kind) for i in r.issues[1:3])
            mark = "*" if r.corner.index in coach.focus else " "
            rival = ""
            if rivals:
                rival = f"{r.rival_gap:>+7.2f}" if r.rival is not None else f"{'':>7}"
                if r.rival_issues:
                    why = phrases.rival_reason(r.rival_issues[0].kind, r.rival_issues[0].amount,
                                               int(r.rival_issues[0].amount))
                    note = (note + "; " if note else "") + f"rival: {why}"
            lines.append(f" {mark}{r.corner.name:<14}{r.loss:>+7.2f}{_num(dbp):>7}"
                         f"{m.v_min - t.v_min:>+7.1f}{m.v_exit - t.v_exit:>+7.1f}{m.gear_apex:>6}{rival}   {note}")
        lines.append("  (loss s vs your best version of each corner; brake m later(+)/earlier(-); "
                     "speeds km/h; rival = s behind the fastest rival there; * = current focus)")
    for s in shifts:
        if s.kind == "limiter":
            lines.append(f"  shifts: on the limiter in gear {s.gear} for {s.limiter_time:.1f}s a lap")
        else:
            best = f"try {s.best_rpm:.0f}" if s.guess else f"best ~{s.best_rpm:.0f}"
            lines.append(f"  shifts: {s.gear}->{s.gear + 1} at {s.typical_rpm:.0f} rpm, {best} "
                         f"({'too early' if s.kind == 'early' else 'too late'}, {s.shifts} shifts)")
    return "\n".join(lines)


def optimum_summary(coach: "Coach") -> dict:
    """What optimum.json holds: the optimum lap corner by corner, and where each part came from."""
    corners = []
    for c in coach.model.corners if coach.model else []:
        own, rival = coach.best.get(c.index), coach.rival_best.get(c.index)
        entry = {"corner": c.name, "best": round(own.time, 3) if own else None}
        if rival:
            entry.update(rival=round(rival.time, 3), rival_driver=rival.driver)
        if own or rival:
            use_rival = rival is not None and (own is None or rival.time < own.time)
            entry["optimum_from"] = rival.driver if use_rival else "you"
        corners.append(entry)
    fastest = coach.rival_laps[0].lap if coach.rival_laps else None
    return {
        "car": coach.info.car, "track": coach.info.track_id, "updated": coach.session_id,
        "personal_best": coach.ref_time, "ideal_lap": coach.ideal_lap, "optimum_lap": coach.optimum_lap,
        "fastest_rival": {"driver": fastest.driver, "lap_time": fastest.lap_time} if fastest else None,
        "corners": corners,
    }


def session_summary(coach: "Coach") -> str:
    laps = coach.lap_times
    valid = [t for _, t, ok in laps if ok]
    lines = ["\n=== Session summary"]
    lines.append(f"  laps: {len(laps)} ({len(valid)} valid)   best: {fmt_time(min(valid) if valid else None)}"
                 f"   all-time best: {fmt_time(coach.ref_time)}   ideal: {fmt_time(coach.ideal_lap)}"
                 f"   optimum: {fmt_time(coach.optimum_lap)}")
    if coach.rival_laps:
        r = coach.rival_laps[0].lap
        lines.append(f"  fastest rival: {r.label} {fmt_time(r.lap_time)}  ({len(coach.rival_laps)} rival laps kept)")
    if len(valid) >= 2:
        lines.append(f"  first valid lap {fmt_time(valid[0])} -> best {fmt_time(min(valid))}  "
                     f"({valid[0] - min(valid):.2f}s found)")
    if coach.model:
        lines.append(f"  {'corner':<14}{'best':>8}{'rival':>8}{'avg loss':>10}   recurring issue")
        for c in coach.model.corners:
            hist = list(coach.history.get(c.index, []))
            best = coach.best.get(c.index)
            if not hist or best is None:
                continue
            rb = coach.rival_best.get(c.index)
            avg = sum(max(h.loss, 0) for h in hist) / len(hist)
            kinds = Counter(h.primary.kind for h in hist if h.primary)
            issue = ""
            if kinds:
                kind, n = kinds.most_common(1)[0]
                issue = f"{phrases.REASON.get(kind, kind)} ({n}/{len(hist)} laps)"
            rival = f"{rb.time:>8.2f}" if rb else f"{'':>8}"
            lines.append(f"  {c.name:<14}{best.time:>8.2f}{rival}{avg:>10.2f}   {issue}")
    return "\n".join(lines)

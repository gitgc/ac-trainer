"""Upshift analysis from your own acceleration data.

At the ideal upshift point the car accelerates equally hard in both gears at that speed.
For every gear we build acceleration-vs-rpm curves from full-throttle running, then compare
gear g at the rpm you shift at against gear g+1 at the rpm you land on. Lower gear still
pulling harder = shifting too early; higher gear pulling harder = too late.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BIN = 250.0
EDGE = 400.0   # how far past the measured rpm range a curve may be read


@dataclass
class ShiftAdvice:
    gear: int                 # shift from this gear to gear + 1
    kind: str                 # early | late | limiter
    typical_rpm: float
    best_rpm: float | None
    shifts: int
    accel_gain: float         # m/s^2 you'd gain at the shift point
    limiter_time: float = 0.0
    guess: bool = False       # best_rpm is a step to try, not a measured optimum


@dataclass
class _Curve:
    centers: np.ndarray
    accel: np.ndarray
    ratio: float              # rpm per m/s

    def at(self, rpm: float) -> float:
        c = self.centers
        if rpm < c[0] - EDGE or rpm > c[-1] + EDGE:
            return float("nan")
        return float(np.interp(rpm, c, self.accel))


def _accel(t: np.ndarray, v: np.ndarray, k: int = 3) -> np.ndarray:
    a = np.full(len(v), np.nan)
    if len(v) > 2 * k:
        dt = t[2 * k:] - t[:-2 * k]
        dv = v[2 * k:] - v[:-2 * k]
        with np.errstate(divide="ignore", invalid="ignore"):
            a[k:-k] = np.where(dt > 1e-3, dv / dt, np.nan)
    return a


def analyze_shifts(laps: list[dict[str, np.ndarray]], rpm_limit: float) -> list[ShiftAdvice]:
    if not laps:
        return []
    if rpm_limit <= 0:
        rpm_limit = max(float(c["rpm"].max()) for c in laps)
    top_gear = max(int(c["gear"].max()) for c in laps)

    pts: dict[int, list[tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
    shifts: dict[int, list[float]] = {}
    limiter: dict[int, float] = {}
    for c in laps:
        t, rpm, gas = c["t"], c["rpm"], c["gas"]
        v = c["speed"] / 3.6
        g = c["gear"].astype(int)
        if len(t) < 20:
            continue
        a = _accel(t, v)
        change = np.flatnonzero(np.diff(g) != 0) + 1   # first sample in a new gear
        since = until = np.full(len(t), np.inf)       # time since / until a gear change
        if len(change):
            idx = np.arange(len(t))
            last = np.searchsorted(change, idx, side="right") - 1
            since = np.where(last >= 0, t - t[change[np.maximum(last, 0)]], np.inf)
            nxt = last + 1
            until = np.where(nxt < len(change), t[change[np.minimum(nxt, len(change) - 1)]] - t, np.inf)
        # wheelspin samples stay in: the acceleration you actually got is what counts
        full = ((gas >= 0.9) & (c["brake"] < 0.02) & (np.abs(c["g_lat"]) < 0.35)
                & (v > 8) & (since > 0.3) & (until > 0.15) & ~np.isnan(a) & (rpm < 0.985 * rpm_limit)
                & (c["in_pit"] < 0.5))
        for gear in np.unique(g[full]):
            if gear < 1:
                continue
            sel = full & (g == gear)
            pts.setdefault(int(gear), []).append((rpm[sel], a[sel], rpm[sel] / v[sel]))
        for i in change:
            if g[i] == g[i - 1] + 1 and g[i - 1] >= 1 and gas[max(i - 3, 0):i].mean() >= 0.9:
                shifts.setdefault(int(g[i - 1]), []).append(float(rpm[max(i - 6, 0):i].max()))
        dt = np.diff(t, prepend=t[0])
        on_lim = (gas >= 0.9) & (rpm >= 0.985 * rpm_limit)
        for gear in np.unique(g[on_lim]):
            if 1 <= gear < top_gear:
                limiter[int(gear)] = limiter.get(int(gear), 0.0) + float(dt[on_lim & (g == gear)].sum())

    curves: dict[int, _Curve] = {}
    for gear, chunks in pts.items():
        r = np.concatenate([x[0] for x in chunks])
        a = np.concatenate([x[1] for x in chunks])
        k = np.concatenate([x[2] for x in chunks])
        bins = np.floor(r / BIN).astype(int)
        centers, med = [], []
        for b in np.unique(bins):
            sel = bins == b
            if sel.sum() >= 4:
                centers.append((b + 0.5) * BIN)
                med.append(float(np.median(a[sel])))
        if len(centers) >= 2:
            curves[gear] = _Curve(np.array(centers), np.array(med), float(np.median(k)))

    n_laps = len(laps)
    out: list[ShiftAdvice] = []
    for gear, rpms in sorted(shifts.items()):
        lim = limiter.get(gear, 0.0) / n_laps
        if lim > 0.3:
            out.append(ShiftAdvice(gear, "limiter", float(np.median(rpms)), rpm_limit * 0.97,
                                   len(rpms), 0.0, lim))
            continue
        lo, hi = curves.get(gear), curves.get(gear + 1)
        if len(rpms) < 3 or lo is None or hi is None:
            continue
        typical = float(np.median(rpms))
        ratio = hi.ratio / lo.ratio

        def diff(x: float) -> float:
            return lo.at(x) - hi.at(x * ratio)

        d0 = diff(typical)
        if np.isnan(d0):
            continue
        thr = max(0.25, 0.05 * abs(lo.at(typical)))
        if d0 > thr and typical < 0.95 * rpm_limit:
            best = None
            for x in np.arange(typical + 100, min(lo.centers[-1] + EDGE, rpm_limit), 100):
                dx = diff(x)
                if not np.isnan(dx) and dx <= 0:
                    best = float(x)
                    break
            guess = False
            if best is None and lo.centers[-1] >= rpm_limit * 0.9:
                best = rpm_limit * 0.97   # still pulling harder right up to the limiter
            elif best is None:
                best, guess = min(rpm_limit * 0.97, typical + 600), True   # no data up there yet: try higher
            if best > typical + 250:
                out.append(ShiftAdvice(gear, "early", typical, best, len(rpms), d0, guess=guess))
        elif d0 < -thr:
            best = None
            for x in np.arange(typical - 100, lo.centers[0] - EDGE, -100):
                dx = diff(x)
                if not np.isnan(dx) and dx >= 0:
                    best = float(x)
                    break
            guess = best is None
            out.append(ShiftAdvice(gear, "late", typical, best if best else typical - 400, len(rpms),
                                   -d0, guess=guess))
    out.sort(key=lambda s: (s.kind != "limiter", -s.accel_gain))
    return out

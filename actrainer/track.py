"""Track model: corners auto-detected from a reference lap.

Every corner owns a segment of the lap running from the speed peak before it to the speed
peak before the next corner, so a segment = braking zone + corner + the straight after it.
Time lost on a straight is then charged to the corner exit that caused it. The run from the
start line to the first braking zone ("lead-in") belongs to the last corner's exit too.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .laps import Trace

BRAKE_ON = 0.1


@dataclass
class Corner:
    index: int
    name: str
    start: float          # segment start (m)
    apex: float           # reference min-speed point (m)
    end: float            # segment end (m)
    brake: float | None   # reference braking point (m), None for lift / flat corners
    done: float           # corner finished: used for the instant verdict
    v_max: float          # reference speed arriving (km/h)
    v_min: float          # reference minimum speed (km/h)
    heavy: bool           # a real braking zone
    direction: int        # sign of lateral g at the apex
    lead_in: float = 0.0  # last corner only: its exit straight continues from 0 to here

    @property
    def marker(self) -> float:
        """Where the driver needs the cue to be finished by."""
        return self.brake if self.brake is not None else max(self.start, self.apex - 80.0)


@dataclass
class TrackModel:
    length: float
    corners: list[Corner] = field(default_factory=list)
    frozen: bool = False
    laps_used: int = 0

    def to_dict(self) -> dict:
        return {"length": self.length, "frozen": self.frozen, "laps_used": self.laps_used,
                "corners": [asdict(c) for c in self.corners]}

    @classmethod
    def from_dict(cls, d: dict) -> "TrackModel":
        return cls(length=d["length"], frozen=d.get("frozen", False),
                   laps_used=d.get("laps_used", 0),
                   corners=[Corner(**c) for c in d.get("corners", [])])

    def rename(self, names: dict[str, str]) -> None:
        for c in self.corners:
            c.name = names.get(str(c.index), f"Turn {c.index}")


def smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1:
        return x.astype(float)
    n |= 1
    pad = n // 2
    padded = np.pad(x.astype(float), pad, mode="wrap")
    return np.convolve(padded, np.ones(n) / n, mode="valid")


def brake_run(brake: np.ndarray, lo: int, hi: int, gap: int = 5,
              threshold: float = BRAKE_ON) -> tuple[int, int, float] | None:
    """Main braking run inside [lo, hi]: (onset idx, release idx, peak pressure)."""
    lo, hi = max(lo, 0), min(hi, len(brake) - 1)
    if hi <= lo:
        return None
    on = np.flatnonzero(brake[lo:hi + 1] > threshold)
    if len(on) == 0:
        return None
    runs: list[list[int]] = [[on[0], on[0]]]
    for i in on[1:]:
        if i - runs[-1][1] <= gap:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    best = max(runs, key=lambda r: brake[lo + r[0]:lo + r[1] + 1].sum())
    a, b = lo + best[0], lo + best[1]
    return a, b, float(brake[a:b + 1].max())


def _minima(v: np.ndarray, w: int) -> list[int]:
    out, i, n = [], w, len(v)
    while i < n - w:
        seg = v[i - w:i + w + 1]
        if v[i] <= seg.min() + 1e-9 and v[i] < seg.max() - 0.5:
            out.append(i)
            i += w
        else:
            i += 1
    return out


def detect_corners(tr: Trace, names: dict[str, str] | None = None,
                   min_drop: float = 8.0, rel_drop: float = 0.04) -> list[Corner]:
    step = tr.step
    v = smooth(tr.speed, int(round(30 / step)))
    mins = _minima(v, max(1, int(25 / step)))

    # drop shallow dips until every remaining minimum is a real corner (peak prominence)
    while mins:
        wrap_max = max(v[mins[-1]:].max(), v[:mins[0] + 1].max())
        between = [v[a:b + 1].max() for a, b in zip(mins, mins[1:])]
        left = [wrap_max] + between
        right = between + [wrap_max]
        margin = [min(l, r) - v[m] - max(min_drop, rel_drop * v[m])
                  for m, l, r in zip(mins, left, right)]
        j = int(np.argmin(margin))
        if margin[j] >= 0:
            break
        mins.pop(j)

    if not mins:
        return []
    lead = int(np.argmax(v[:mins[0] + 1]))
    bounds = [lead] + [a + int(np.argmax(v[a:b + 1])) for a, b in zip(mins, mins[1:])] + [len(v) - 1]
    corners = []
    names = names or {}
    for k, m in enumerate(mins):
        s_i, e_i = bounds[k], bounds[k + 1]
        lo = max(s_i, m - int(60 / step))
        hi = min(e_i, m + int(60 / step))
        apex_i = lo + int(np.argmin(tr.speed[lo:hi + 1]))
        run = brake_run(tr.brake, s_i, apex_i + int(15 / step))
        v_max = float(tr.speed[s_i:apex_i + 1].max())
        v_min = float(tr.speed[apex_i])
        full = np.flatnonzero(tr.gas[apex_i:e_i + 1] >= 0.95)
        apex_d, end_d = float(tr.d[apex_i]), float(tr.d[e_i])
        done = min(apex_d + 250.0, end_d - 20.0)
        if len(full):
            done = min(done, float(tr.d[apex_i + full[0]]) + 30.0)
        done = max(done, min(apex_d + 20.0, (apex_d + end_d) / 2))
        g = tr.g_lat[max(apex_i - 10, 0):apex_i + 11]
        corners.append(Corner(
            index=k + 1, name=names.get(str(k + 1), f"Turn {k + 1}"),
            start=float(tr.d[s_i]), apex=apex_d, end=end_d,
            brake=float(tr.d[run[0]]) if run else None, done=done,
            v_max=v_max, v_min=v_min,
            heavy=bool(run) and v_max - v_min >= 30.0,
            direction=1 if g.mean() >= 0 else -1,
        ))
    if corners:
        corners[-1].end = float(tr.d[-1])
        corners[-1].lead_in = float(tr.d[lead])
    return corners

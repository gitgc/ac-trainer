"""Visual overlay: what the ACTrainerLink CSP app draws on the track.

After every lap the coach writes overlay.json next to the rival feed. The CSP app reloads it
when it changes and draws, in the 3D world:
  - your best line: per corner, the lap that holds your best time through it, coloured by
    phase (braking / coasting / throttle)
  - for the corners being worked on, the braking marker to aim for (your best braking point,
    the rival's when the focus comes from a faster driver, or the "try 5 m later" point while
    exploring), the throttle point and the apex
Positions are world x/z from the telemetry; the app finds the height with a ground raycast.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .laps import Trace

if TYPE_CHECKING:
    from .coach import Coach

LINE_STEP = 4.0      # metres between points of the drawn line
SECTION_BEFORE = 80  # focus mode: line drawn from this far before the braking point...
THROTTLE_KINDS = {"coasting", "throttle_late", "full_throttle_late", "wheelspin"}
PHASE_BRAKE, PHASE_COAST, PHASE_THROTTLE = 0, 1, 2


def default_overlay_path() -> Path:
    from .rivals import default_feed_path
    return default_feed_path().with_name("overlay.json")


def _marker(tr: Trace, d: float) -> list[float]:
    """[x, z, heading x, heading z] at lap distance d."""
    x, z = float(np.interp(d, tr.d, tr.x)), float(np.interp(d, tr.d, tr.z))
    hx = float(np.interp(d + 3, tr.d, tr.x) - np.interp(d - 3, tr.d, tr.x))
    hz = float(np.interp(d + 3, tr.d, tr.z) - np.interp(d - 3, tr.d, tr.z))
    n = max((hx * hx + hz * hz) ** 0.5, 1e-6)
    return [round(x, 2), round(z, 2), round(hx / n, 3), round(hz / n, 3)]


def _best_line(coach: "Coach") -> list[list[float]]:
    """Your best version of every corner, stitched into one lap."""
    corners, L = coach.model.corners, coach.L
    grid = np.arange(0.0, L, LINE_STEP)
    owner = np.full(len(grid), len(corners) - 1)   # before the first corner: last one's exit
    for k, c in enumerate(corners):
        owner[(grid >= c.start) & (grid < c.end)] = k
    x, z, phase = np.zeros(len(grid)), np.zeros(len(grid)), np.full(len(grid), PHASE_COAST)
    for k, c in enumerate(corners):
        sel = owner == k
        best = coach.best.get(c.index)
        tr = best.trace if best is not None else coach.ref_trace
        if not sel.any() or tr is None:
            continue
        g = grid[sel]
        x[sel], z[sel] = np.interp(g, tr.d, tr.x), np.interp(g, tr.d, tr.z)
        brake, gas = np.interp(g, tr.d, tr.brake), np.interp(g, tr.d, tr.gas)
        phase[sel] = np.where(brake > 0.1, PHASE_BRAKE, np.where(gas > 0.2, PHASE_THROTTLE, PHASE_COAST))
    return [[round(float(a), 2), round(float(b), 2), int(p)] for a, b, p in zip(x, z, phase)]


def build_overlay(coach: "Coach") -> dict | None:
    model = coach.model
    if model is None or not model.corners or coach.ref_trace is None or not coach.L:
        return None
    corners = []
    for c in model.corners:
        f = coach.focus.get(c.index)
        best = coach.best.get(c.index)
        tr, m = (best.trace, best.metrics) if best is not None else (coach.ref_trace, None)
        rival = coach.rival_best.get(c.index)
        if f is not None and f.source == "rival" and rival is not None:
            tr, m = rival.trace, rival.metrics
        short = c.name.replace("Turn ", "T")
        brake = m.brake_point if m is not None else c.brake
        brake_label = f"{short} BRAKE"
        if f is not None and f.kind == "explore" and f.base_bp is not None:
            brake, brake_label = f.base_bp + f.amount, f"{short} BRAKE +{f.amount:.0f} m"
        elif f is not None and f.source == "rival" and rival is not None:
            brake_label = f"{short} BRAKE ({rival.driver})"
        gas = m.throttle_on if m is not None else None
        apex = m.apex if m is not None else c.apex
        entry = {
            "n": c.index, "name": c.name, "focus": f.kind if f is not None else "",
            "from": round(max((brake if brake is not None else c.marker) - SECTION_BEFORE, c.start), 1),
            "to": round(c.done, 1),
            "brake": _marker(tr, brake) if brake is not None and c.heavy else None,
            "brake_label": brake_label,
            "apex": _marker(tr, apex),
            "gas": _marker(tr, gas) if gas is not None else None,
            "gas_label": f"{short} THROTTLE",
            "show_brake": f is None or f.kind not in THROTTLE_KINDS,
            "show_gas": f is not None and f.kind in THROTTLE_KINDS,
        }
        corners.append(entry)
    return {
        "v": 1, "ts": int(time.time()), "track": coach.info.track, "layout": coach.info.track_config,
        "car": coach.info.car, "length": round(coach.L, 1), "step": LINE_STEP,
        "line": _best_line(coach), "corners": corners,
    }


class OverlayWriter:
    """Writes overlay.json atomically, so the CSP app never reads half a file."""

    def __init__(self, path: Path, log=print):
        self.path = path
        self.log = log
        self._failed = False

    def write(self, coach: "Coach") -> None:
        data = build_overlay(coach)
        if data is None:
            return
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
            self._failed = False
        except OSError as e:
            if not self._failed:
                self.log(f"[overlay] cannot write {self.path}: {e}")
            self._failed = True

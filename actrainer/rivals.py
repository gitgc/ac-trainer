"""Other drivers' laps, recorded in-game by the ACTrainerLink CSP app.

Shared memory and UDP only describe your own car. The CSP Lua app sees every car and appends
each completed lap to a JSON-lines file; we tail it, keep the quickest laps of the same car
model on the same track, and the coach uses them as an extra reference: per corner, the
optimum is the better of your best and the fastest rival's.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .sample import FIELDS, SessionInfo

ROW = ("lap_time", "pos", "speed", "gas", "brake", "gear", "rpm", "x", "z")


def _documents() -> Path:
    if sys.platform == "win32":   # Documents may be redirected (e.g. into OneDrive)
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
            return Path(buf.value)
    return Path.home() / "Documents"


def default_feed_path() -> Path:
    """Where the ACTrainerLink CSP app writes (Documents/Assetto Corsa/actrainer)."""
    return _documents() / "Assetto Corsa" / "actrainer" / "rival_laps.jsonl"


@dataclass
class RivalLap:
    driver: str
    model: str
    lap_time: float
    cols: dict[str, np.ndarray]
    pedals: bool          # False when the server doesn't replicate other cars' pedals
    ai: bool = False

    @property
    def label(self) -> str:
        return self.driver or "a rival"


def parse_line(line: str) -> tuple[dict, RivalLap] | None:
    try:
        d = json.loads(line)
        rows = np.asarray(d["rows"], dtype=float)
        lap_time = float(d["lap_ms"]) / 1000.0
    except (ValueError, KeyError, TypeError):
        return None
    if rows.ndim != 2 or rows.shape[1] != len(ROW) or len(rows) < 100 or lap_time <= 0:
        return None
    # the first rows of a lap can still carry the previous lap's timer
    start = int(np.argmax(rows[:, 0] < 2.0))
    rows = rows[start:]
    n = len(rows)
    cols = {name: np.zeros(n) for name in FIELDS}
    for k, name in enumerate(ROW):
        cols[name] = rows[:, k].copy()
    cols["laps"][:] = 0
    pedals = bool(cols["gas"].max() > 0.05 or cols["brake"].max() > 0.05)
    return d, RivalLap(str(d.get("driver", "")), str(d.get("model", "")), lap_time, cols, pedals,
                       bool(d.get("ai", False)))


class RivalFeed:
    """Tails the JSON-lines file written by the CSP app."""

    def __init__(self, path: Path, info: SessionInfo, any_model: bool = False):
        self.path = path
        self.info = info
        self.any_model = any_model
        self._offset = 0
        self._partial = ""

    def _matches(self, d: dict, lap: RivalLap) -> bool:
        track = str(d.get("track", ""))
        layout = str(d.get("layout", ""))
        if track and track != self.info.track:
            return False
        if layout and self.info.track_config and layout != self.info.track_config:
            return False
        return self.any_model or not lap.model or lap.model == self.info.car

    def poll(self) -> list[RivalLap]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self._offset:          # the app rotated the file
            self._offset, self._partial = 0, ""
        if size == self._offset:
            return []
        with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self._offset)
            chunk = fh.read()
            self._offset = fh.tell()
        text = self._partial + chunk
        lines = text.split("\n")
        self._partial = lines.pop()       # incomplete last line, finish it next time
        out = []
        for line in lines:
            parsed = parse_line(line) if line.strip() else None
            if parsed and self._matches(*parsed):
                out.append(parsed[1])
        return out


def rival_line(driver: str, model: str, track: str, cols: dict[str, np.ndarray], lap_time: float,
               pedals: bool = True) -> str:
    """Build a feed line the way the CSP app does (used by the demo and the tests)."""
    rows = np.column_stack([cols[name] for name in ROW])
    if not pedals:
        rows[:, 3:5] = 0
    return json.dumps({"v": 1, "track": track, "layout": "", "model": model, "driver": driver,
                       "ai": False, "lap_ms": round(lap_time * 1000), "rows": np.round(rows, 6).tolist()})

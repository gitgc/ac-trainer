"""Per car/track data and raw session recordings.

tracks/<car__track>/
    track.json        corner map
    reference.npz     your personal best lap
    laps/*.npz        the optimum-lap library: every lap (yours or a rival's) that holds the
                      best time through at least one corner, so the next session starts
                      knowing all of them
    optimum.json      human-readable summary of the optimum lap
"""
from __future__ import annotations

import csv
import gzip
import json
import time
from pathlib import Path
from typing import Iterator

import numpy as np

from .sample import FIELDS, Sample, SessionInfo, sample_from_row, sample_to_row
from .track import TrackModel


class Store:
    """Files under <data_dir>/tracks/<car__track>/. root=None keeps everything in memory."""

    def __init__(self, root: Path | None, info: SessionInfo):
        self.dir = root / "tracks" / info.key if root else None
        self._mem: dict[str, object] = {}

    def _path(self, name: str) -> Path | None:
        if self.dir is None:
            return None
        self.dir.mkdir(parents=True, exist_ok=True)
        return self.dir / name

    def load_model(self) -> TrackModel | None:
        if self.dir is None:
            return self._mem.get("model")
        p = self.dir / "track.json"
        if not p.exists():
            return None
        return TrackModel.from_dict(json.loads(p.read_text()))

    def save_model(self, model: TrackModel) -> None:
        p = self._path("track.json")
        if p is None:
            self._mem["model"] = model
        else:
            p.write_text(json.dumps(model.to_dict(), indent=1))

    def load_reference(self) -> tuple[dict[str, np.ndarray], float] | None:
        if self.dir is None:
            return self._mem.get("ref")
        p = self.dir / "reference.npz"
        if not p.exists():
            return None
        with np.load(p) as z:
            cols = {name: z[name] for name in FIELDS if name in z}
            return cols, float(z["__lap_time"])

    def save_reference(self, cols: dict[str, np.ndarray], lap_time: float) -> None:
        p = self._path("reference.npz")
        if p is None:
            self._mem["ref"] = (cols, lap_time)
        else:
            np.savez_compressed(p, __lap_time=np.array(lap_time), **cols)

    # -- optimum-lap library -------------------------------------------------------------
    def save_lap(self, name: str, cols: dict[str, np.ndarray], lap_time: float, meta: dict) -> None:
        if self.dir is None:
            self._mem.setdefault("laps", {})[name] = (cols, lap_time, dict(meta))
            return
        folder = self.dir / "laps"
        folder.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(folder / f"{name}.npz", __lap_time=np.array(lap_time),
                            __meta=np.array(json.dumps(meta)), **cols)

    def load_laps(self) -> list[tuple[str, dict[str, np.ndarray], float, dict]]:
        if self.dir is None:
            return [(k, c, t, m) for k, (c, t, m) in self._mem.get("laps", {}).items()]
        out = []
        for p in sorted((self.dir / "laps").glob("*.npz")) if (self.dir / "laps").exists() else []:
            try:
                with np.load(p) as z:
                    cols = {name: z[name] for name in FIELDS if name in z}
                    out.append((p.stem, cols, float(z["__lap_time"]), json.loads(str(z["__meta"]))))
            except (OSError, ValueError, KeyError):
                print(f"[store] skipping unreadable {p}")
        return out

    def prune_laps(self, keep: set[str]) -> None:
        if self.dir is None:
            laps = self._mem.get("laps", {})
            for k in [k for k in laps if k not in keep]:
                del laps[k]
            return
        folder = self.dir / "laps"
        for p in folder.glob("*.npz") if folder.exists() else []:
            if p.stem not in keep:
                p.unlink()

    def save_optimum(self, summary: dict) -> None:
        p = self._path("optimum.json")
        if p is None:
            self._mem["optimum"] = summary
        else:
            p.write_text(json.dumps(summary, indent=1))

    def load_meta(self) -> dict:
        if self.dir is None:
            return dict(self._mem.get("meta", {}))
        p = self.dir / "meta.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def save_meta(self, meta: dict) -> None:
        p = self._path("meta.json")
        if p is None:
            self._mem["meta"] = dict(meta)
        else:
            p.write_text(json.dumps(meta, indent=1))


class SessionWriter:
    def __init__(self, root: Path, info: SessionInfo):
        folder = root / "sessions"
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{time.strftime('%Y%m%d-%H%M%S')}_{info.key}.csv.gz"
        self._fh = gzip.open(self.path, "wt", encoding="utf-8", newline="")
        self._fh.write("# " + json.dumps(info.to_dict()) + "\n")
        self._fh.write(",".join(FIELDS) + "\n")
        self._t0: float | None = None

    def write(self, s: Sample) -> None:
        if self._t0 is None:
            self._t0 = s.t
        row = sample_to_row(s)
        rel = format(s.t - self._t0, ".4f")
        self._fh.write(rel + row[row.index(","):] + "\n")

    def close(self) -> None:
        self._fh.close()


def read_session(path: str | Path) -> tuple[SessionInfo, Iterator[Sample]]:
    fh = gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz") else open(path, encoding="utf-8")
    first = fh.readline()
    info = SessionInfo.from_dict(json.loads(first[1:])) if first.startswith("#") else SessionInfo()
    header = first.strip().split(",") if not first.startswith("#") else fh.readline().strip().split(",")

    def samples() -> Iterator[Sample]:
        with fh:
            for row in csv.reader(fh):
                if row:
                    yield sample_from_row(header, row)

    return info, samples()

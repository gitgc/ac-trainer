"""Play back a recorded session (.csv.gz written by the coach)."""
from __future__ import annotations

import time

from ..sample import Sample, SessionInfo
from ..storage import read_session
from .base import SourceClosed


class ReplaySource:
    name = "replay"

    def __init__(self, path: str, speed: float = 1.0):
        self.path = path
        self.speed = speed

    def open(self) -> SessionInfo:
        info, self._samples = read_session(self.path)
        info.source = self.name
        self._wall0: float | None = None
        return info

    def read(self) -> Sample:
        try:
            s = next(self._samples)
        except StopIteration:
            raise SourceClosed("end of recording") from None
        if self.speed > 0:
            if self._wall0 is None:
                self._wall0, self._t0 = time.monotonic(), s.t
            wait = self._wall0 + (s.t - self._t0) / self.speed - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        return s

    def close(self) -> None:
        pass

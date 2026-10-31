"""Common interface for telemetry sources."""
from __future__ import annotations

from typing import Protocol

from ..sample import Sample, SessionInfo


class SourceError(RuntimeError):
    """The source can't be used at all (wrong platform, bad file...)."""


class SourceClosed(Exception):
    """The game (or recording) went away; the session is over."""


class SessionChanged(Exception):
    """A different car or track was loaded; restart the coach."""


class Source(Protocol):
    name: str

    def open(self) -> SessionInfo | None:
        """Connect. Returns None while the game isn't running yet."""

    def read(self) -> Sample | None:
        """Block until the next sample is due. None = nothing new (paused, replay...)."""

    def close(self) -> None: ...

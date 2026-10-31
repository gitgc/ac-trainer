"""Telemetry sources."""
from .base import SessionChanged, Source, SourceClosed, SourceError

__all__ = ["SessionChanged", "Source", "SourceClosed", "SourceError", "make_source"]


def make_source(cfg) -> Source:
    t = cfg.telemetry
    if t.source == "shared_memory":
        from .shared_memory import SharedMemorySource
        return SharedMemorySource(t.poll_hz)
    if t.source == "udp":
        from .udp import UdpSource
        return UdpSource(t.udp_host, t.udp_port)
    raise SourceError(f"unknown telemetry source {t.source!r} (use shared_memory or udp)")

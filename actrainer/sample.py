"""Telemetry sample and session info shared by every telemetry source."""
from __future__ import annotations

import re
from dataclasses import dataclass, fields


@dataclass(slots=True)
class Sample:
    t: float = 0.0          # source clock, seconds (monotonic)
    lap_time: float = 0.0   # current lap time from the game, seconds
    laps: int = 0           # completed laps
    last_lap: float = 0.0   # last lap time, seconds (0 = none yet)
    pos: float = 0.0        # normalized spline position 0..1
    speed: float = 0.0      # km/h
    gas: float = 0.0        # 0..1
    brake: float = 0.0      # 0..1
    clutch: float = 0.0     # 0..1
    gear: int = 0           # -1 reverse, 0 neutral, 1.. forward gears
    rpm: float = 0.0
    steer: float = 0.0
    g_lat: float = 0.0
    g_lon: float = 0.0
    x: float = 0.0
    z: float = 0.0
    # longitudinal slip estimate per wheel: wheel surface speed / car speed - 1
    # (negative = locking under braking, positive = spinning under power)
    slip_fl: float = 0.0
    slip_fr: float = 0.0
    slip_rl: float = 0.0
    slip_rr: float = 0.0
    tyres_out: int = 0
    in_pit: int = 0
    sector: int = 0


FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Sample))
INT_FIELDS = frozenset({"laps", "gear", "tyres_out", "in_pit", "sector"})
_FORMATS = {"t": ".4f", "lap_time": ".4f", "last_lap": ".4f", "pos": ".7f",
            "x": ".2f", "z": ".2f", "rpm": ".0f", "speed": ".3f"}


def sample_to_row(s: Sample) -> str:
    out = []
    for name in FIELDS:
        v = getattr(s, name)
        if name in INT_FIELDS:
            out.append(str(int(v)))
        else:
            out.append(format(v, _FORMATS.get(name, ".4g")))
    return ",".join(out)


def sample_from_row(header: list[str], row: list[str]) -> Sample:
    kw = {}
    for name, raw in zip(header, row):
        if name not in FIELDS:
            continue
        kw[name] = int(float(raw)) if name in INT_FIELDS else float(raw)
    return Sample(**kw)


@dataclass
class SessionInfo:
    car: str = "unknown"
    track: str = "unknown"
    track_config: str = ""
    track_length: float = 0.0   # metres, 0 = unknown (estimated from the first lap)
    rpm_limit: float = 0.0      # 0 = unknown (taken from the highest rpm seen)
    driver: str = ""
    source: str = ""

    @property
    def track_id(self) -> str:
        return f"{self.track}/{self.track_config}" if self.track_config else self.track

    @property
    def key(self) -> str:
        """Filesystem-safe id for this car + track combination."""
        raw = f"{self.car}__{self.track}" + (f"__{self.track_config}" if self.track_config else "")
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict) -> "SessionInfo":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

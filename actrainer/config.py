"""Configuration, loaded from a TOML file (see config.example.toml)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class TelemetryConfig:
    source: str = "shared_memory"   # shared_memory | udp
    udp_host: str = "127.0.0.1"     # PC running Assetto Corsa (udp source only)
    udp_port: int = 9996
    poll_hz: float = 60.0
    max_tyres_out: int = 2          # more tyres than this off track = lap invalid


@dataclass
class VoiceConfig:
    backend: str = "auto"    # auto | sapi | say | espeak | print
    voice: str = ""          # part of the voice name, e.g. "Zira" or "David"
    rate: int = 2            # -10 (slow) .. 10 (fast)
    volume: int = 100        # 0..100
    audio_device: str = ""   # part of the output device name, e.g. "Headphones"


@dataclass
class CoachConfig:
    verbosity: str = "normal"       # quiet | normal | chatty
    lead_time: float = 2.5          # cue finishes this many seconds before the braking point
    max_focus: int = 2              # corners worked on at the same time
    corner_feedback: bool = True    # short verdict right after a focus corner
    explore_brake_points: bool = True
    lap_summary: bool = True


@dataclass
class RivalsConfig:
    enabled: bool = True     # learn from other cars (needs the ACTrainerLink CSP app in game)
    feed: str = ""           # default: Documents/Assetto Corsa/actrainer/rival_laps.jsonl
    any_model: bool = False  # also learn from different cars (not recommended)


@dataclass
class Config:
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    coach: CoachConfig = field(default_factory=CoachConfig)
    rivals: RivalsConfig = field(default_factory=RivalsConfig)
    data_dir: str = "~/AssettoTrainer"
    record_sessions: bool = True
    # track id ("ks_brands_hatch/indy") -> {"1": "Paddock Hill", ...}
    corner_names: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser()


def _apply(obj, values: dict, where: str) -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in values.items():
        if key not in known:
            print(f"[config] ignoring unknown setting {where}{key}")
            continue
        current = getattr(obj, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply(current, value, f"{where}{key}.")
        elif isinstance(current, bool):
            setattr(obj, key, bool(value))
        elif isinstance(current, (int, float)) and not isinstance(value, bool):
            setattr(obj, key, type(current)(value))
        else:
            setattr(obj, key, value)


def load_config(path: str | Path | None) -> Config:
    cfg = Config()
    if path is None:
        default = Path("config.toml")
        if not default.exists():
            return cfg
        path = default
    with open(path, "rb") as fh:
        _apply(cfg, tomllib.load(fh), "")
    return cfg

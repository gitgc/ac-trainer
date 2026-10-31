"""Command line entry point: `python -m actrainer [run|demo|replay|analyze|voices|say]`."""
from __future__ import annotations

import argparse
import sys
import time

from .coach import Coach
from .config import Config, load_config
from .sources import SessionChanged, SourceClosed, SourceError, make_source
from .speech import SpeechManager, TextSpeech, list_voices, make_backend
from .storage import SessionWriter, Store


RIVAL_POLL = 120   # samples between checks of the rival feed (~2 s)


def _drive(source, info, cfg: Config, speech, store: Store, record: bool,
           tip_listener=None, rivals=None) -> None:
    writer = SessionWriter(cfg.data_path, info) if record else None
    if writer:
        print(f"[coach] recording to {writer.path}")
    coach = Coach(info, cfg, speech, store, tip_listener=tip_listener)
    coach.start()
    n = 0
    try:
        while True:
            s = source.read()
            if s is None:
                continue
            if writer:
                writer.write(s)
            coach.on_sample(s)
            n += 1
            if rivals is not None and n % RIVAL_POLL == 0:
                for lap in rivals.poll():
                    coach.add_rival_lap(lap)
    except SessionChanged as e:
        print(f"[coach] new session: {e}")
    except SourceClosed as e:
        print(f"[coach] {e}")
    finally:
        coach.close()
        if writer:
            writer.close()
        source.close()


def _rival_feed(cfg: Config, info):
    if not cfg.rivals.enabled:
        return None
    from pathlib import Path

    from .rivals import RivalFeed, default_feed_path

    path = Path(cfg.rivals.feed).expanduser() if cfg.rivals.feed else default_feed_path()
    if path.exists():
        print(f"[rivals] reading other drivers' laps from {path}")
    else:
        print(f"[rivals] no rival laps yet - install the ACTrainerLink CSP app to learn from "
              f"faster drivers (looking for {path})")
    return RivalFeed(path, info, any_model=cfg.rivals.any_model)


def _speech(cfg: Config) -> SpeechManager:
    speech = SpeechManager(lambda: make_backend(cfg.voice))
    speech.start()
    return speech


def run_live(cfg: Config) -> None:
    speech = _speech(cfg)
    try:
        while True:
            source = make_source(cfg)
            info, waited = None, 0
            while info is None:
                info = source.open()
                if info is None:
                    if waited % 20 == 0:
                        print("Waiting for Assetto Corsa - start a session from Content Manager...")
                    waited += 1
                    time.sleep(1.0)
            _drive(source, info, cfg, speech, Store(cfg.data_path, info), cfg.record_sessions,
                   rivals=_rival_feed(cfg, info))
    finally:
        speech.stop()


def run_demo(cfg: Config, speed: float, laps: int | None, learn: bool, rival: bool) -> None:
    from .sources.sim import SimSource, sim_rival_feed

    source = SimSource(speed=speed, laps=laps, learn=learn)
    info = source.open()
    print("Demo: simulated driver on a fictional circuit. Ctrl+C to stop.")
    feed = sim_rival_feed(info) if rival else None
    speech = _speech(cfg)
    try:
        _drive(source, info, cfg, speech, Store(None, info), record=False,
               tip_listener=source.apply_tip, rivals=feed)
    finally:
        speech.stop(flush=10)


def run_replay(cfg: Config, path: str, speed: float, silent: bool) -> None:
    from .sources.replay import ReplaySource

    source = ReplaySource(path, speed=speed)
    info = source.open()
    store = Store(None, info)   # don't touch your saved bests
    if silent:
        _drive(source, info, cfg, TextSpeech(), store, record=False)
        return
    speech = _speech(cfg)
    try:
        _drive(source, info, cfg, speech, store, record=False)
    finally:
        speech.stop(flush=10)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="actrainer", description="Assetto Corsa voice driving coach")
    ap.add_argument("-c", "--config", help="config file (default: ./config.toml if present)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="coach you live in Assetto Corsa (default)")
    demo = sub.add_parser("demo", help="hear the coach with a simulated driver, no game needed")
    demo.add_argument("--speed", type=float, default=1.0, help="time multiplier (0 = as fast as possible)")
    demo.add_argument("--laps", type=int, default=None)
    demo.add_argument("--no-learn", action="store_true", help="the demo driver ignores the advice")
    demo.add_argument("--no-rival", action="store_true", help="no faster rival car on track")
    rp = sub.add_parser("replay", help="re-run the coach (with voice) on a recorded session")
    rp.add_argument("file")
    rp.add_argument("--speed", type=float, default=1.0)
    an = sub.add_parser("analyze", help="print the lap by lap analysis of a recorded session")
    an.add_argument("file")
    sub.add_parser("voices", help="list voices and audio outputs")
    sy = sub.add_parser("say", help="speak a test line (check voice / headphones)")
    sy.add_argument("text", nargs="*")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)

    try:
        if args.cmd in (None, "run"):
            run_live(cfg)
        elif args.cmd == "demo":
            run_demo(cfg, args.speed, args.laps, not args.no_learn, not args.no_rival)
        elif args.cmd == "replay":
            run_replay(cfg, args.file, args.speed, silent=False)
        elif args.cmd == "analyze":
            run_replay(cfg, args.file, 0, silent=True)
        elif args.cmd == "voices":
            list_voices()
        elif args.cmd == "say":
            speech = _speech(cfg)
            speech.say(" ".join(args.text) or "Radio check. Turn 1, brake 10 meters later.", gated=False)
            speech.stop(flush=15)
    except SourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nbye")
    return 0

import threading
import time

from actrainer.config import Config
from actrainer.sources.replay import ReplaySource
from actrainer.sources.base import SourceClosed
from actrainer.sources.sim import SimSource
from actrainer.speech import CUE, FEEDBACK, INFO, SpeechManager
from actrainer.storage import SessionWriter, read_session


class FakeVoice:
    words_per_second = 50.0

    def __init__(self, seconds=0.15):
        self.spoken, self.cut = [], []
        self.seconds = seconds

    def speak(self, text, should_stop):
        end = time.monotonic() + self.seconds
        while time.monotonic() < end:
            if should_stop():
                self.cut.append(text)
                return
            time.sleep(0.005)
        self.spoken.append(text)


def manager(voice):
    m = SpeechManager(lambda: voice, log=lambda *_: None)
    m.start()
    return m


def wait_idle(m, timeout=3):
    end = time.monotonic() + timeout
    while m.busy() and time.monotonic() < end:
        time.sleep(0.01)


def test_priority_and_dedupe():
    voice = FakeVoice()
    m = manager(voice)
    calm = threading.Event()
    m.gate = lambda duration, priority: calm.is_set()
    m.say("lap 1", INFO, key="lap")
    m.say("lap 2", INFO, key="lap")          # replaces the queued one
    m.say("turn 3 brake later", CUE, key="cue3")
    m.say("better", FEEDBACK)
    time.sleep(0.1)
    assert voice.spoken == []                # driver busy: nothing yet
    calm.set()
    wait_idle(m)
    m.stop()
    assert voice.spoken == ["turn 3 brake later", "better", "lap 2"]


def test_cue_interrupts_small_talk():
    voice = FakeVoice(seconds=1.0)
    m = manager(voice)
    m.say("a long lap summary", INFO)
    time.sleep(0.1)
    m.say("turn 1 brake later", CUE)
    wait_idle(m)
    m.stop()
    assert voice.cut == ["a long lap summary"]
    assert voice.spoken == ["turn 1 brake later"]


def test_cancel_and_expiry():
    voice = FakeVoice()
    m = manager(voice)
    m.gate = lambda duration, priority: False
    m.say("stale cue", CUE, key="cue2")
    m.say("old news", INFO, ttl=0.05)
    m.cancel("cue")
    time.sleep(0.15)
    m.gate = lambda duration, priority: True
    time.sleep(0.15)
    m.stop()
    assert voice.spoken == []


def test_record_and_replay(tmp_path):
    src = SimSource(speed=0, laps=1, learn=False)
    info = src.open()
    writer = SessionWriter(tmp_path, info)
    n = 0
    try:
        while True:
            writer.write(src.read())
            n += 1
    except SourceClosed:
        pass
    writer.close()
    info2, samples = read_session(writer.path)
    assert info2.track == "demo_ring" and info2.track_length == info.track_length
    rows = list(samples)
    assert len(rows) == n
    assert rows[0].t == 0.0 and rows[-1].laps == 2

    rp = ReplaySource(str(writer.path), speed=0)
    assert rp.open().car == info.car
    count = 0
    try:
        while True:
            rp.read()
            count += 1
    except SourceClosed:
        pass
    assert count == n


def test_config_loading(tmp_path):
    from actrainer.config import load_config

    p = tmp_path / "c.toml"
    p.write_text('[voice]\nvoice = "Zira"\nrate = 3\n[coach]\nverbosity = "quiet"\n'
                 '[corner_names."ks_brands_hatch/indy"]\n1 = "Paddock Hill"\n')
    cfg = load_config(p)
    assert cfg.voice.voice == "Zira" and cfg.voice.rate == 3
    assert cfg.coach.verbosity == "quiet"
    assert cfg.corner_names["ks_brands_hatch/indy"]["1"] == "Paddock Hill"
    assert isinstance(Config().telemetry.poll_hz, float)

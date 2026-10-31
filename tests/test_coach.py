"""End to end: the coach talking to the simulated driver."""
import time

from actrainer import phrases
from actrainer.cli import _drive
from actrainer.coach import Coach, crossed
from actrainer.config import Config
from actrainer.sources.sim import Driver, SimSource
from actrainer.speech import CUE, TextSpeech
from actrainer.storage import Store


def coach_session(laps=10, driver=None, learn=True, store_root=None):
    src = SimSource(speed=0, laps=laps, driver=driver, learn=learn)
    info = src.open()
    speech = TextSpeech(log=None)
    store = Store(store_root, info)
    _drive(src, info, Config(), speech, store, record=False, tip_listener=src.apply_tip)
    return speech, store, info


def test_crossed_handles_the_line():
    assert crossed(10, 20, 15) and not crossed(10, 20, 25)
    assert crossed(3290, 5, 3295) and crossed(3290, 5, 2)
    assert not crossed(3290, 5, 100)


def test_coach_makes_the_demo_driver_faster(capsys):
    speech, _, _ = coach_session(laps=10)
    out = capsys.readouterr().out
    lines = [t for _, t in speech.lines]
    assert "Turn 1, brake 20 meters later" in lines
    assert any(t.startswith("Turn 3, no coasting") for t in lines)
    assert any(t.startswith("Hold third gear longer") for t in lines)
    assert any(t.startswith("New personal best") for t in lines)
    first = float(out.split("first valid lap ")[1].split()[0].split(":")[1])
    best = float(out.split("-> best ")[1].split()[0].split(":")[1])
    assert first - best > 1.0


def test_clean_driver_hears_little(capsys):
    speech, _, _ = coach_session(laps=6, driver=Driver(), learn=False)
    cues = [t for p, t in speech.lines if p == CUE and "try braking" not in t]
    assert len(cues) <= 3, cues


def test_reference_and_track_persist(tmp_path, capsys):
    coach_session(laps=3, store_root=tmp_path)
    src = SimSource(speed=0, laps=1)
    info = src.open()
    store = Store(tmp_path, info)
    speech = TextSpeech(log=None)
    coach = Coach(info, Config(), speech, store)
    coach.start()
    assert coach.model is not None and len(coach.model.corners) == 6
    assert coach.ref_time is not None and len(coach.best) == 6
    assert speech.lines[0][1].startswith("Coach ready. Your best here is")


def test_lap_analysis_is_quick(demo_laps):
    _, info, laps, _ = demo_laps
    speech = TextSpeech(log=None)
    coach = Coach(info, Config(), speech, Store(None, info), log=lambda *_: None)
    for lap in laps:
        coach._on_lap(lap)
    t0 = time.perf_counter()
    coach._on_lap(laps[-1])
    assert time.perf_counter() - t0 < 0.5


def test_phrases():
    assert phrases.lap_time(81.24) == "1 21 point 2"
    assert phrases.lap_time(65.04) == "1 oh 5 point 0"
    assert phrases.lap_time(59.97) == "1 oh 0 point 0"
    assert phrases.lap_time(42.31) == "42 point 3"
    assert phrases.delta(0.34) == "plus 3 tenths"
    assert phrases.delta(-0.05) == "minus 5 hundredths"
    assert phrases.delta(1.26) == "plus 1.3 seconds"
    assert phrases.cue("brake_early", "Turn 4", 63) == "Turn 4, brake 20 meters later"
    assert phrases.cue("gear_high", "Turn 2", gear_no=3) == "Turn 2, use third"


def cautious_driver():
    from actrainer.sources.sim import Habit

    d = Driver(shift_rpm={1: 7350.0, 2: 7150.0, 3: 7350.0, 4: 7400.0, 5: 7400.0})
    for k in range(6):
        d.habits[k] = Habit(speed_factor=0.95)
    return d


def rival_session(laps, store_root=None, driver=None):
    from actrainer.sources.sim import sim_rival_feed

    src = SimSource(speed=0, laps=laps, driver=driver or cautious_driver(), learn=True)
    info = src.open()
    speech = TextSpeech(log=None)
    _drive(src, info, Config(), speech, Store(store_root, info), record=False,
           tip_listener=src.apply_tip, rivals=sim_rival_feed(info))
    return speech, info


def test_rival_reveals_what_self_analysis_cannot(capsys):
    # a driver who is equally cautious everywhere looks consistent against themselves...
    alone, _, _ = coach_session(laps=5, driver=cautious_driver(), learn=False)
    assert not [t for p, t in alone.lines if p == CUE and "apex" in t]
    # ...but a faster car of the same model shows where the time is
    speech, _ = rival_session(laps=6)
    lines = [t for _, t in speech.lines]
    assert any("Rival Rick is" in t and "quicker" in t for t in lines)
    assert any(t.endswith("carry more speed to the apex") for t in lines)
    assert any(t.startswith("Optimum lap, with the rivals' best corners") for t in lines)


def test_library_survives_to_the_next_session(tmp_path, capsys):
    rival_session(laps=4, store_root=tmp_path)
    info = SimSource(speed=0).open()
    store = Store(tmp_path, info)
    saved = store.load_laps()
    kinds = {meta["kind"] for _, _, _, meta in saved}
    assert kinds == {"own", "rival"}
    assert (tmp_path / "tracks" / info.key / "optimum.json").exists()

    coach = Coach(info, Config(), TextSpeech(log=None), store, log=lambda *_: None)
    assert len(coach.best) == 6 and len(coach.rival_best) == 6
    assert coach.optimum_lap < coach.ideal_lap <= coach.ref_time + 1e-6
    coach.start()
    assert "Optimum lap" in coach.speech.lines[0][1]


def test_library_prunes_laps_that_hold_nothing(tmp_path, capsys):
    coach_session(laps=6, store_root=tmp_path)
    info = SimSource(speed=0).open()
    store = Store(tmp_path, info)
    coach = Coach(info, Config(), TextSpeech(log=None), store, log=lambda *_: None)
    holders = {b.lap_id for b in coach.best.values()}
    assert {name for name, *_ in store.load_laps()} == holders

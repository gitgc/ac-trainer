import numpy as np

from actrainer.rivals import RivalFeed, parse_line, rival_line
from actrainer.sample import SessionInfo


def _cols(n=400, lap_time=80.0):
    t = np.linspace(0, lap_time, n)
    return {"lap_time": t, "pos": t / lap_time, "speed": np.full(n, 150.0), "gas": np.ones(n),
            "brake": np.zeros(n), "gear": np.full(n, 4.0), "rpm": np.full(n, 6000.0),
            "x": np.zeros(n), "z": np.zeros(n)}


INFO = SessionInfo(car="ks_mazda_mx5_cup", track="magione", track_length=3300)


def test_parse_line_roundtrip():
    line = rival_line("Marco", "ks_mazda_mx5_cup", "magione", _cols(), 80.0, pedals=False)
    d, lap = parse_line(line)
    assert lap.driver == "Marco" and lap.lap_time == 80.0 and not lap.pedals
    assert len(lap.cols["pos"]) == 400 and lap.cols["speed"][10] == 150.0


def test_parse_drops_stale_timer_rows():
    cols = _cols()
    cols["lap_time"][:3] = [79.9, 79.95, 80.0]      # previous lap's clock on the first frames
    _, lap = parse_line(rival_line("A", "m", "t", cols, 80.0))
    assert lap.cols["lap_time"][0] < 2.0


def test_feed_filters_and_tails(tmp_path):
    path = tmp_path / "rival_laps.jsonl"
    feed = RivalFeed(path, INFO)
    assert feed.poll() == []
    good = rival_line("Marco", "ks_mazda_mx5_cup", "magione", _cols(), 80.0)
    other_car = rival_line("Lena", "ks_bmw_m3_e30", "magione", _cols(), 79.0)
    other_track = rival_line("Ann", "ks_mazda_mx5_cup", "monza", _cols(), 79.0)
    with open(path, "w") as fh:
        fh.write(good + "\n" + other_car + "\n" + other_track + "\n" + good[:50])
    laps = feed.poll()
    assert [lap.driver for lap in laps] == ["Marco"]
    with open(path, "a") as fh:            # the half-written line gets finished
        fh.write(good[50:] + "\n")
    assert [lap.driver for lap in feed.poll()] == ["Marco"]
    assert feed.poll() == []
    # any_model: both cars on this track (Marco twice, Lena), still not the other track
    assert sorted(lap.driver for lap in RivalFeed(path, INFO, any_model=True).poll()) == ["Lena", "Marco", "Marco"]


def test_garbage_lines_are_ignored():
    assert parse_line("not json") is None
    assert parse_line('{"rows": [[1,2]], "lap_ms": 1}') is None

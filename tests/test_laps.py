import numpy as np

from actrainer.laps import LapRecorder, build_trace, samples_to_columns
from actrainer.sample import Sample

L = 1000.0


def stream(laps=3, per_lap=200, start_pos=0.6, lap_s=50.0, **overrides):
    """A partial first lap (joining mid-lap), then `laps` full constant-speed laps."""
    out, t = [], 0.0
    pos, lap_t, done = start_pos, start_pos * lap_s, 0
    dt = lap_s / per_lap
    while True:
        out.append(Sample(t=t, lap_time=lap_t, laps=done, last_lap=lap_s if done else 0.0,
                          pos=pos % 1.0, speed=L / lap_s * 3.6, gas=1.0, gear=3))
        if done == laps + 1:
            break
        t += dt
        lap_t += dt
        pos += dt / lap_s
        if pos >= 1.0:
            pos -= 1.0
            done += 1
            lap_t = pos * lap_s
    for k, v in overrides.items():
        for s in out:
            setattr(s, k, v)
    return out


def test_laps_split_and_first_lap_partial():
    laps = []
    rec = LapRecorder(laps.append)
    for s in stream(laps=3):
        rec.feed(s)
    assert [lap.complete for lap in laps] == [False, True, True, True]
    assert all(lap.valid for lap in laps[1:])
    assert laps[1].time == 50.0


def test_track_limits_invalidates():
    laps = []
    rec = LapRecorder(laps.append)
    samples = stream(laps=3)
    for s in samples[300:310]:
        s.tyres_out = 3
    for s in samples:
        rec.feed(s)
    assert [lap.invalid_reason for lap in laps[1:]] == ["", "track limits", ""]


def test_session_restart_drops_lap():
    laps = []
    rec = LapRecorder(laps.append)
    samples = stream(laps=2, gas=0.5)          # gas=0.5 marks the abandoned run
    for s in samples[:150]:
        rec.feed(s)
    restarted = stream(laps=2, start_pos=0.0)  # restart at the line: three full laps
    for s in restarted:
        rec.feed(s)
    valid = [lap for lap in laps if lap.valid]
    assert len(valid) == 3
    assert all(s.gas == 1.0 for lap in valid for s in lap.samples)


def test_timer_reset_before_lap_count():
    # in the game the lap timer resets a frame or two before the lap counter goes up
    laps = []
    rec = LapRecorder(laps.append)
    samples = stream(laps=3)
    counted = [i for i in range(1, len(samples)) if samples[i].laps == samples[i - 1].laps + 1]
    for i in counted[:-1]:   # the stream ends on the last crossing: leave that one as it is
        samples[i].laps, samples[i].last_lap = samples[i - 1].laps, samples[i - 1].last_lap
    for s in samples:
        rec.feed(s)
    assert [lap.valid for lap in laps] == [False, True, True, True]
    assert [lap.time for lap in laps[1:]] == [50.0, 50.0, 50.0]
    assert all(len(lap.samples) > 150 for lap in laps[1:])

def test_spline_offset_is_learnt():
    laps = []
    rec = LapRecorder(laps.append)
    samples = stream(laps=4)
    for s in samples:
        s.pos = (s.pos + 0.3) % 1.0       # AI spline starts 300 m after the timing line
        rec.feed(s)
    assert abs(rec.pos_offset - 0.3) < 0.01
    assert laps[-1].valid
    assert min(s.pos for s in laps[-1].samples) < 0.01


def test_trace_unwraps_line_crossing():
    samples = stream(laps=3)
    laps = []
    rec = LapRecorder(laps.append)
    for s in samples:
        rec.feed(s)
    lap = laps[1]
    lap.samples.insert(1, Sample(pos=0.9995, lap_time=0.01, laps=lap.samples[0].laps, speed=72))
    tr = build_trace(lap.columns(), L, lap.time)
    assert tr.d[0] == 0 and abs(tr.end - L) < 1e-6
    assert np.all(np.diff(tr.t) >= 0)
    assert abs(tr.time_at(500) - 25.0) < 0.3


def test_partial_trace_starts_mid_lap():
    samples = stream(laps=2)[50:120]
    tr = build_trace(samples_to_columns(samples), L, None, at_line=False)
    assert tr.start > 0
    assert tr.idx(tr.start) == 0
    assert tr.covers(tr.start + 10, tr.end - 10)

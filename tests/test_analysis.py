"""Analysis on simulated laps where we know exactly what the driver does wrong."""
from actrainer.analysis import analyze_corners, diagnose, extract
from actrainer.gears import analyze_shifts
from actrainer.track import TrackModel, detect_corners


def model_for(info, traces):
    best = min(traces, key=lambda t: t.t[-1])
    return TrackModel(info.track_length, detect_corners(best))


def kinds(result):
    return {i.kind for i in result.issues}


def test_corners_match_the_circuit(demo_laps):
    src, info, laps, traces = demo_laps
    model = model_for(info, traces)
    apexes = src.corner_apex_distances()
    assert len(model.corners) == len(apexes)
    for c, a in zip(model.corners, apexes):
        assert abs(c.apex - a) < 80
        assert c.start < c.apex < c.end
    # segments tile the lap: lead-in + corners = whole lap
    assert model.corners[0].start == model.corners[-1].lead_in
    for a, b in zip(model.corners, model.corners[1:]):
        assert a.end == b.start


def test_segment_times_add_up_to_lap_time(demo_laps):
    _, info, laps, traces = demo_laps
    model = model_for(info, traces)
    tr = traces[1]
    total = sum(extract(tr, c, 7600).time for c in model.corners)
    assert abs(total - tr.t[-1]) < 0.01


def test_planted_habits_are_found(demo_laps):
    _, info, laps, traces = demo_laps
    model = model_for(info, traces)
    res = {r.corner.index: r for r in analyze_corners(traces[-1], model, {}, 7600)}
    t1, t3, t5 = res[1], res[3], res[5]
    assert t1.primary.kind == "brake_early"
    assert 15 <= t1.primary.amount <= 30          # habit: 22 m early
    assert "coasting" in kinds(t3)                 # habit: 30 m off throttle after the apex
    assert {"lockup", "apex_slow"} <= kinds(t5)    # habit: 90% of the grip, locks the fronts


def test_relative_brake_point(demo_laps):
    _, info, laps, traces = demo_laps
    model = model_for(info, traces)
    c = model.corners[1]
    best = extract(traces[0], c, 7600)
    early = extract(traces[0], c, 7600)
    early.brake_point -= 15
    late = extract(traces[0], c, 7600)
    late.brake_point += 15
    late.v_min -= 6                               # ran deep and scrubbed the speed off
    assert any(i.kind == "brake_early" and i.amount >= 15 for i in diagnose(early, best, c))
    assert diagnose(late, best, c)[0].kind == "brake_late"


def test_clean_driver_is_left_alone(clean_laps):
    _, info, laps, traces = clean_laps
    model = model_for(info, traces)
    nag = {"brake_early", "coasting", "throttle_late", "apex_slow", "brake_soft", "lockup"}
    for r in analyze_corners(traces[-1], model, {}, 7600):
        assert not (kinds(r) & nag), (r.corner.name, kinds(r))


def test_short_shifting_detected(demo_laps):
    _, info, laps, _ = demo_laps
    advice = {a.gear: a for a in analyze_shifts([lap.columns() for lap in laps], 7600)}
    assert advice[3].kind == "early" and advice[3].typical_rpm < 6300
    assert advice[2].kind == "early"


def test_good_shifting_left_alone(clean_laps):
    _, info, laps, _ = clean_laps
    advice = analyze_shifts([lap.columns() for lap in laps], 7600)
    assert not [a for a in advice if a.gear in (2, 3, 4)], advice

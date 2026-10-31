"""The overlay file the CSP app draws from."""
import json

import numpy as np

from actrainer.cli import _drive
from actrainer.config import Config
from actrainer.overlay import LINE_STEP, OverlayWriter
from actrainer.sources.sim import SimSource
from actrainer.speech import TextSpeech
from actrainer.storage import Store


def test_overlay_marks_the_focus_corners(tmp_path, capsys):
    path = tmp_path / "overlay.json"
    src = SimSource(speed=0, laps=4, learn=False)
    info = src.open()
    writer = OverlayWriter(path)
    _drive(src, info, Config(), TextSpeech(log=None), Store(None, info), record=False, overlay=writer)
    assert not path.with_suffix(".tmp").exists()
    data = json.loads(path.read_text())
    assert (data["car"], data["track"], data["layout"]) == (info.car, info.track, info.track_config)
    assert data["step"] == LINE_STEP
    assert abs(len(data["line"]) * LINE_STEP - info.track_length) <= LINE_STEP

    # the line follows the car's path and changes phase: braking, coasting, throttle
    xz = np.array([p[:2] for p in data["line"]])
    gaps = np.hypot(*np.diff(xz, axis=0).T)
    assert gaps.max() < 3 * LINE_STEP
    assert {p[2] for p in data["line"]} == {0, 1, 2}

    corners = data["corners"]
    assert len(corners) == 6
    focus = [c for c in corners if c["focus"]]
    assert focus, "the demo driver has mistakes to work on"
    for c in corners:
        x, z, hx, hz = c["apex"]
        assert abs(np.hypot(hx, hz) - 1) < 1e-2
        assert np.hypot(*(xz - [x, z]).T).min() < 10   # on (or next to) the drawn line
    # braking markers only for real braking zones, and the turn 1 marker is the best one
    t1 = next(c for c in corners if c["n"] == 1)
    assert t1["brake"] is not None and t1["brake_label"].startswith("T1 BRAKE")
    assert t1["from"] < t1["to"]


import pytest

from actrainer.laps import LapRecorder, build_trace
from actrainer.sources.base import SourceClosed
from actrainer.sources.sim import Driver, SimSource, demo_driver


def drive(laps: int, driver: Driver | None = None, seed: int = 7):
    src = SimSource(speed=0, laps=laps, seed=seed, driver=driver or demo_driver(), learn=False)
    info = src.open()
    done = []
    rec = LapRecorder(done.append)
    try:
        while True:
            rec.feed(src.read())
    except SourceClosed:
        pass
    return src, info, [lap for lap in done if lap.valid]


@pytest.fixture(scope="session")
def demo_laps():
    src, info, laps = drive(4)
    traces = [build_trace(lap.columns(), info.track_length, lap.time) for lap in laps]
    return src, info, laps, traces


@pytest.fixture(scope="session")
def clean_laps():
    src, info, laps = drive(4, driver=Driver())
    traces = [build_trace(lap.columns(), info.track_length, lap.time) for lap in laps]
    return src, info, laps, traces

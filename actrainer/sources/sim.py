"""Built-in driving simulator: a fictional circuit and a driver with habits to fix.

Used by `actrainer demo` (hear the coach without the game) and by the tests. The physics
is a simple point-mass model with a torque curve, gearing, drag and a grip limit - enough to
produce realistic-looking traces and genuine braking / throttle / shift-point mistakes.
"""
from __future__ import annotations

import bisect
import math
import random
import time
from dataclasses import dataclass, field

import numpy as np

from ..sample import Sample, SessionInfo
from .base import SourceClosed

G = 9.81
DS = 0.5              # envelope grid (m)
PHYSICS_HZ = 240
OUTPUT_EVERY = 4      # -> 60 Hz samples

# (length m, radius m (0 = straight), direction +1 left / -1 right)
DEMO_TRACK = [
    (520, 0, 0), (95, 45, 1), (330, 0, 0), (130, 110, -1), (240, 0, 0), (80, 28, -1),
    (680, 0, 0), (160, 220, 1), (260, 0, 0), (100, 65, 1), (420, 0, 0), (75, 38, -1),
    (210, 0, 0),
]


@dataclass
class Car:
    mass: float = 1250.0
    mu: float = 1.35
    cda: float = 0.75
    rolling: float = 150.0
    wheel_r: float = 0.31
    gears: tuple[float, ...] = (3.10, 2.15, 1.62, 1.30, 1.08, 0.92)
    final: float = 4.1
    eff: float = 0.88
    rpm_limit: float = 7600.0
    torque_rpm: tuple[float, ...] = (1000, 3000, 4800, 6000, 7000, 7600)
    torque_nm: tuple[float, ...] = (220, 330, 395, 370, 300, 250)

    def rpm(self, v: float, gear: int) -> float:
        return v / self.wheel_r * self.gears[gear - 1] * self.final * 60 / (2 * math.pi)

    def torque(self, rpm: float) -> float:
        xs, ys = self.torque_rpm, self.torque_nm
        if rpm <= xs[0]:
            return ys[0]
        j = min(bisect.bisect_right(xs, rpm), len(xs) - 1)
        f = (rpm - xs[j - 1]) / (xs[j] - xs[j - 1])
        return ys[j - 1] + f * (ys[j] - ys[j - 1])

    def drive(self, v: float, gear: int) -> float:
        rpm = self.rpm(v, gear)
        if rpm >= self.rpm_limit:
            return 0.0
        torque = self.torque(rpm)
        return torque * self.gears[gear - 1] * self.final * self.eff / self.wheel_r

    def drag(self, v: float) -> float:
        return 0.5 * 1.2 * self.cda * v * v + self.rolling


@dataclass
class Habit:
    brake_early: float = 0.0      # metres before the ideal braking point
    speed_factor: float = 0.985   # share of the grip limit used mid-corner
    throttle_delay: float = 0.0   # metres after the apex before picking up the throttle
    lockup: bool = False
    wheelspin: bool = False       # stamps on the throttle out of the corner


@dataclass
class Driver:
    habits: dict[int, Habit] = field(default_factory=dict)
    shift_rpm: dict[int, float] = field(default_factory=lambda: {g: 7300.0 for g in range(1, 6)})
    downshift_rpm: float = 6600.0
    pickup: float = 0.97          # how close to the target speed counts as "there"

    def habit(self, corner: int) -> Habit:
        return self.habits.setdefault(corner, Habit())


def demo_driver() -> Driver:
    d = Driver()
    d.habits[0] = Habit(brake_early=22.0)                    # Turn 1: brakes far too early
    d.habits[2] = Habit(throttle_delay=30.0)                 # hairpin: coasts before the throttle
    d.habits[4] = Habit(speed_factor=0.90, lockup=True)      # over-slows and locks up
    d.shift_rpm[2] = 6000.0                                  # short-shifts 2 -> 3
    d.shift_rpm[3] = 6100.0                                  # and 3 -> 4
    return d


def rival_driver() -> Driver:
    """A tidy, committed driver: the faster car the demo driver gets compared with."""
    d = Driver(shift_rpm={1: 7350.0, 2: 7150.0, 3: 7350.0, 4: 7400.0, 5: 7400.0})
    for k in range(6):
        d.habits[k] = Habit(speed_factor=0.995)
    return d


class _ListFeed:
    """Hands out pre-recorded rival laps, a couple at a time, like the CSP app would."""

    def __init__(self, laps):
        self._laps = list(laps)
        self._polls = 0

    def poll(self):
        self._polls += 1
        if self._polls < 30 or not self._laps:   # rivals show up during your first laps
            return []
        return [self._laps.pop(0)]


def sim_rival_feed(info, laps: int = 3, pedals: bool = False):
    from ..laps import LapRecorder
    from ..rivals import RivalLap

    src = SimSource(speed=0, laps=laps, seed=21, driver=rival_driver(), learn=False)
    src.open()
    done = []
    rec = LapRecorder(done.append)
    try:
        while True:
            rec.feed(src.read())
    except SourceClosed:
        pass
    out = []
    for lap in done:
        if lap.valid:
            cols = lap.columns()
            if not pedals:   # online servers often don't send other cars' pedals
                cols["gas"][:] = 0
                cols["brake"][:] = 0
            out.append(RivalLap("Rival Rick", info.car, lap.time, cols, pedals))
    return _ListFeed(out)


class SimSource:
    name = "sim"

    def __init__(self, speed: float = 1.0, laps: int | None = None, seed: int = 7,
                 driver: Driver | None = None, learn: bool = True, track=DEMO_TRACK):
        self.speed = speed
        self.max_laps = laps
        self.rng = random.Random(seed)
        self.driver = driver or demo_driver()
        self.learn = learn
        self.car = Car()
        self._pending: list[tuple] = []
        self._build_track(track)

    # -- track ---------------------------------------------------------------------------
    def _build_track(self, pieces) -> None:
        self.length = float(sum(p[0] for p in pieces))
        n = int(round(self.length / DS))
        self.kappa = np.zeros(n)
        self.dirn = np.zeros(n)
        self.corners: list[dict] = []
        d = 0.0
        for length, radius, direction in pieces:
            a, b = int(round(d / DS)), int(round((d + length) / DS))
            if radius:
                m = (a + b) // 2
                self.kappa[a:m] = 1.0 / radius
                self.kappa[m:b] = np.linspace(1.0, 0.45, b - m) / radius   # line opens after the apex
                self.dirn[a:b] = direction
                self.corners.append({"entry": a, "exit": b, "apex": (a + b) // 2, "radius": radius})
            d += length

    def corner_apex_distances(self) -> list[float]:
        return [c["apex"] * DS for c in self.corners]

    def _build_envelope(self) -> None:
        car, n = self.car, len(self.kappa)
        lim = np.full(n, 95.0)
        self.coast = np.zeros(n, bool)
        self.delay = np.zeros(n, bool)
        self.lock = np.zeros(n, bool)
        self.spin = np.zeros(n, bool)
        for k, c in enumerate(self.corners):
            h = self.driver.habit(k)
            factor = h.speed_factor * (1 + self.rng.gauss(0, 0.004))
            factor = min(factor, 1.0)
            vc = math.sqrt(car.mu * G * c["radius"]) * factor
            a, m, b = c["entry"], c["apex"], c["exit"]
            lim[a:b] = np.sqrt(car.mu * G / self.kappa[a:b]) * factor
            early = max(0.0, h.brake_early + self.rng.gauss(0, 2.5))
            e = int(early / DS)
            if e:
                lim[max(a - e, 0):a] = vc
                self.coast[max(a - e, 0):a] = True
            delay = max(0.0, h.throttle_delay + self.rng.gauss(0, 3.0))
            self.delay[m:m + int(delay / DS)] = True
            if h.lockup:
                self.lock[max(a - e - int(120 / DS), 0):max(a - e, 0)] = True
            if h.wheelspin:
                self.spin[m:b + int(80 / DS)] = True
        env = lim.copy()
        for _ in range(2):  # backward braking pass, twice round to handle the wrap
            for i in range(n - 1, -1, -1):
                nxt = env[(i + 1) % n]
                b = car.mu * G + car.drag(nxt) / car.mass
                env[i] = min(lim[i], math.sqrt(nxt * nxt + 2 * b * DS))
        self.env = env

    def _build_layout(self) -> None:
        """Centre line in world x/z, so a point of the lap is in the same place every lap."""
        n = len(self.kappa)
        heading = np.cumsum(self.kappa * self.dirn) * DS
        x, z = np.cumsum(np.cos(heading)) * DS, np.cumsum(np.sin(heading)) * DS
        f = np.arange(1, n + 1) / n   # the fictional layout doesn't quite close: spread the gap
        self.line_x, self.line_z = x - f * x[-1], z - f * z[-1]

    # -- source interface ----------------------------------------------------------------
    def open(self) -> SessionInfo:
        self.t = 0.0
        self.d = self.length - 700.0      # out lap: start before the last corner
        self.v = 30.0
        self.gear = 3
        self.shift_timer = 0.0
        self.last_down = -1.0
        self.brake = 0.0
        self.laps = 0
        self.lap_start = 0.0
        self.last_lap = 0.0
        self._build_envelope()
        self._build_layout()
        self._wall0 = time.monotonic()
        return SessionInfo(car="demo_gt", track="demo_ring", track_length=self.length,
                           rpm_limit=self.car.rpm_limit, driver="Demo Driver", source="sim")

    def close(self) -> None:
        pass

    def read(self) -> Sample:
        if self.max_laps is not None and self.laps > self.max_laps:
            raise SourceClosed("demo finished")
        dt = 1.0 / PHYSICS_HZ
        for _ in range(OUTPUT_EVERY):
            out = self._step(dt)
        if self.speed > 0:
            due = self._wall0 + self.t / self.speed
            wait = due - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        return out

    def _step(self, dt: float) -> Sample:
        car, drv = self.car, self.driver
        n = len(self.env)
        dm = self.d % self.length
        i = int(dm / DS) % n
        v = self.v
        target = float(self.env[int(((dm + v * 0.05) % self.length) / DS) % n])

        brake = 0.0
        if v > target + 0.05:
            need = (v - target) / 0.05
            brake = min(1.0, max(0.0, (need - car.drag(v) / car.mass) / (car.mu * G)))
        brake = min(brake, self.brake + 10 * dt)      # human brake application rate
        self.brake = brake

        gas = 0.0
        if brake < 0.02:
            if self.coast[i]:
                gas = 0.0
            elif self.delay[i]:
                gas = 0.0
            elif v < target * drv.pickup:
                gas = 1.0
            else:
                gas = min(1.0, self._resistance(v, i) / max(car.drive(v, self.gear), 1.0))

        if self.shift_timer > 0:
            self.shift_timer -= dt
        elif gas > 0.5 and self.gear < len(car.gears) and car.rpm(v, self.gear) >= drv.shift_rpm.get(self.gear, 7300):
            self.gear += 1
            self.shift_timer = 0.1
        elif (brake > 0.05 and self.gear > 1 and car.rpm(v, self.gear - 1) < drv.downshift_rpm
              and self.t - self.last_down > 0.25):
            self.gear -= 1
            self.last_down = self.t
        elif self.gear > 1 and car.rpm(v, self.gear) < 1800:
            self.gear -= 1

        drive = gas * car.drive(v, self.gear) if self.shift_timer <= 0 else 0.0
        grip = car.mu * G * car.mass * 0.55
        spin = drive > grip and bool(self.spin[i])
        if drive > grip and not spin:
            gas *= grip / drive          # a tidy driver feeds the throttle in at the traction limit
        drive = min(drive, grip)
        a = (drive - self._resistance(v, i)) / car.mass - brake * car.mu * G
        self.v = max(1.0, v + a * dt)
        step = self.v * dt
        self.d += step
        self.t += dt

        lap_dist = self.d - self.length * self.laps
        if lap_dist >= self.length:
            self.laps += 1
            self.last_lap = self.t - self.lap_start
            self.lap_start = self.t
            self._new_lap()

        lap_t = self.t - self.lap_start
        slip_f = -0.35 if (self.lock[i] and brake > 0.8) else self.rng.gauss(0, 0.004)
        slip_r = 0.18 if spin else abs(self.rng.gauss(0, 0.004))
        rpm = max(900.0, min(car.rpm(self.v, self.gear), car.rpm_limit * 1.06))
        return Sample(
            t=self.t, lap_time=lap_t, laps=self.laps, last_lap=self.last_lap,
            pos=(self.d % self.length) / self.length, speed=self.v * 3.6, gas=gas, brake=brake,
            clutch=1.0, gear=self.gear, rpm=rpm, steer=float(self.dirn[i] * min(1.0, self.kappa[i] * 20)),
            g_lat=float(self.dirn[i] * self.kappa[i] * self.v ** 2 / G), g_lon=a / G,
            x=float(self.line_x[i]), z=float(self.line_z[i]), slip_fl=slip_f, slip_fr=slip_f, slip_rl=slip_r, slip_rr=slip_r,
        )

    def _resistance(self, v: float, i: int) -> float:
        # aero + rolling drag, plus tyre scrub while cornering (~6% of the lateral force)
        return self.car.drag(v) + 0.06 * self.car.mass * v * v * self.kappa[i]

    # -- the demo driver listens to the coach -----------------------------------------------
    def apply_tip(self, apex: float | None, kind: str, value: float | None = None) -> None:
        if self.learn:
            self._pending.append((apex, kind, value))

    def _new_lap(self) -> None:
        for apex, kind, value in self._pending:
            if self.rng.random() > 0.8:
                continue  # sometimes it takes a couple of reminders
            if kind.startswith("shift_"):
                g = int(value[0]) if value else 0
                cur = self.driver.shift_rpm.get(g, 7300.0)
                goal = value[1] if value and value[1] else cur + 800
                self.driver.shift_rpm[g] = cur + 0.7 * (goal - cur)
                continue
            dists = self.corner_apex_distances()
            k = int(np.argmin([abs(apex - x) for x in dists]))
            if abs(dists[k] - apex) > 150:
                continue
            h = self.driver.habit(k)
            if kind == "brake_early":
                h.brake_early *= 0.35
            elif kind == "explore":
                h.brake_early = max(0.0, h.brake_early - 5)
            elif kind in ("coasting", "throttle_late", "full_throttle_late", "exit_slow"):
                h.throttle_delay *= 0.35
            elif kind == "apex_slow":
                h.speed_factor = min(0.99, 1 - (1 - h.speed_factor) * 0.4)
            elif kind in ("lockup", "brake_late"):
                h.lockup = False
            elif kind == "wheelspin":
                h.wheelspin = False
        self._pending.clear()
        self._build_envelope()

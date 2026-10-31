"""Per-corner metrics and diagnosis.

Two kinds of checks:
  * relative - against the best version of that corner you've driven (per-corner bests
    from any lap: "you braked 12 m earlier than on your best lap through here")
  * absolute - things that are slow whatever your best is: coasting into a corner (braked
    too early), dawdling on part throttle after the apex, using less grip than the car shows
    in similar corners, braking well below the decel the car manages elsewhere, lockups.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .laps import Trace
from .track import Corner, TrackModel, brake_run


@dataclass
class CornerMetrics:
    time: float              # time through the segment (or up to corner.done when partial)
    t_entry: float           # segment start -> reference apex
    t_exit: float            # reference apex -> segment end (or done)
    brake_point: float | None
    brake_peak: float
    brake_ramp: float        # seconds from first touch to 90% of peak pressure
    brake_release: float | None
    brake_decel: float | None  # m/s^2 averaged over the straight-line part of the braking
    decel_point: float | None  # where the car starts slowing hard - from speed alone, so it
                               # also works for rivals whose pedals aren't transmitted
    v_max: float
    v_min: float
    apex: float
    g_peak: float            # lateral g around the apex
    gear_apex: int
    rpm_apex: float
    throttle_on: float | None
    full_throttle: float | None
    v_exit: float
    coast: float             # metres with neither pedal between braking and throttle
    coast_entry: float       # ...of which before turning in (= braked too early)
    plateau: float           # metres on part throttle after the apex, below the grip limit
    lockup: float            # metres with the fronts locking
    wheelspin: float         # metres with the rears spinning on exit
    lift_mid: bool           # backed out of the throttle after picking it up
    overrev: bool            # over-revved on a downshift
    late_downshift: bool     # still downshifting after releasing the brake
    partial: bool = False


def extract(tr: Trace, c: Corner, rpm_limit: float, upto: float | None = None) -> CornerMetrics | None:
    end = c.end if upto is None else upto
    if not tr.covers(c.start, end):
        return None
    step = tr.step
    i_s, i_e, i_ra = tr.idx(c.start), tr.idx(end), tr.idx(c.apex)
    w = int(80 / step)
    lo, hi = max(i_s, i_ra - w), min(i_e, i_ra + w)
    apex_i = lo + int(np.argmin(tr.speed[lo:hi + 1]))
    g_win = np.abs(tr.g_lat[max(apex_i - int(40 / step), i_s):min(apex_i + int(40 / step), i_e) + 1])
    g_peak = float(np.percentile(g_win, 85)) if len(g_win) else 0.0

    run = brake_run(tr.brake, i_s - int(10 / step), min(apex_i + int(30 / step), i_e))
    bp_i = br_i = None
    peak = ramp = 0.0
    decel = None
    if run:
        bp_i, br_i, peak = run
        reach = np.flatnonzero(tr.brake[bp_i:br_i + 1] >= 0.9 * peak)
        ramp = float(tr.t[bp_i + reach[0]] - tr.t[bp_i]) if len(reach) else 0.0
        turning = np.flatnonzero(np.abs(tr.g_lat[bp_i:apex_i + 1]) >= max(0.3, 0.5 * g_peak))
        ti = bp_i + int(turning[0]) if len(turning) else apex_i
        dist = tr.d[ti] - tr.d[bp_i]
        if dist >= 20:
            v0, v1 = tr.speed[bp_i] / 3.6, tr.speed[ti] / 3.6
            decel = float((v0 * v0 - v1 * v1) / (2 * dist))

    v = tr.speed / 3.6
    slowing = np.convolve(-v * np.gradient(v, tr.d), np.ones(3) / 3, mode="same")
    drun = brake_run(slowing, i_s - int(10 / step), apex_i, threshold=3.0)

    gas = tr.gas
    if br_i is not None:
        anchor = br_i
    else:
        a, b = max(i_s, apex_i - w), min(i_e, apex_i + w)
        anchor = a + int(np.argmin(gas[a:b + 1]))
    th_i = ft_i = None
    if gas[anchor] < 0.2 or br_i is not None:
        on = np.flatnonzero(gas[anchor:i_e + 1] >= 0.2)
        th_i = anchor + int(on[0]) if len(on) else None
    base = th_i if th_i is not None else anchor
    full = np.flatnonzero(gas[base:i_e + 1] >= 0.95)
    if len(full):
        ft_i = base + int(full[0])

    coast = coast_entry = 0.0
    idle = (gas < 0.1) & (tr.brake < 0.05)
    lat = np.abs(tr.g_lat)
    g_ref = max(g_peak, 0.6)
    if br_i is not None:
        stop = th_i if th_i is not None else i_e
        coast = float(np.count_nonzero(idle[br_i:max(stop, br_i)]) * step)
        pre = slice(br_i, max(min(stop, apex_i), br_i))
        coast_entry = float(np.count_nonzero(idle[pre] & (lat[pre] < 0.5 * g_ref)) * step)

    # part throttle after the apex while the car isn't using its grip = waiting to commit
    commit = np.flatnonzero(gas[apex_i:i_e + 1] >= 0.8)
    j = apex_i + int(commit[0]) if len(commit) else i_e
    seg = slice(apex_i, j)
    plateau = float(np.count_nonzero((gas[seg] >= 0.05) & (gas[seg] < 0.5) & (tr.brake[seg] < 0.05)
                                     & (lat[seg] < 0.75 * g_ref)) * step)

    entry = slice(i_s, apex_i + 1)
    lockup = float(np.count_nonzero((tr.slip_f[entry] < -0.15) & (tr.brake[entry] > 0.2)
                                    & (tr.speed[entry] > 30)) * step)
    exit_ = slice(apex_i, i_e + 1)
    wheelspin = float(np.count_nonzero((tr.slip_r[exit_] > 0.12) & (gas[exit_] > 0.4)
                                       & (tr.speed[exit_] > 15)) * step)

    lift_mid = False
    if th_i is not None:
        seg_g = gas[th_i:(ft_i if ft_i is not None else i_e) + 1]
        if len(seg_g) > 1:
            runmax = np.maximum.accumulate(seg_g)
            lift_mid = bool(np.any((runmax >= 0.5) & (runmax - seg_g > 0.35) & (seg_g < 0.3)))

    overrev = late_down = False
    if bp_i is not None:
        if rpm_limit > 0:
            overrev = bool(tr.rpm[bp_i:apex_i + 1].max() > rpm_limit * 1.03)
        g = tr.gear[i_s:min(apex_i + int(20 / step), i_e) + 1]
        downs = np.flatnonzero(np.diff(g) < 0)
        if len(downs):
            late_down = bool(i_s + downs[-1] + 1 > br_i + int(5 / step))

    t_ra = tr.t[min(i_ra, i_e)]
    time = float(tr.t[i_e] - tr.t[i_s])
    t_exit = float(tr.t[i_e] - t_ra)
    if c.lead_in > 0 and upto is None:
        # the last corner's exit straight carries on past the line, up to the first braking zone
        if tr.start > step or tr.end < c.lead_in:
            return None
        extra = tr.time_at(c.lead_in) - float(tr.t[0])
        time += extra
        t_exit += extra
    return CornerMetrics(
        time=time, t_entry=float(t_ra - tr.t[i_s]), t_exit=t_exit,
        brake_point=float(tr.d[bp_i]) if bp_i is not None else None,
        brake_peak=peak, brake_ramp=ramp,
        brake_release=float(tr.d[br_i]) if br_i is not None else None, brake_decel=decel,
        decel_point=float(tr.d[drun[0]]) if drun else None,
        v_max=float(tr.speed[i_s:apex_i + 1].max()), v_min=float(tr.speed[apex_i]),
        apex=float(tr.d[apex_i]), g_peak=g_peak,
        gear_apex=int(tr.gear[apex_i]), rpm_apex=float(tr.rpm[apex_i]),
        throttle_on=float(tr.d[th_i]) if th_i is not None else None,
        full_throttle=float(tr.d[ft_i]) if ft_i is not None else None,
        v_exit=float(tr.speed[tr.idx(min(c.done, end))]),
        coast=coast, coast_entry=coast_entry, plateau=plateau,
        lockup=lockup, wheelspin=wheelspin, lift_mid=lift_mid,
        overrev=overrev, late_downshift=late_down, partial=upto is not None,
    )


# kind -> (phase, weight, typical magnitude, base score for issues that matter even without
#          a time loss against the target)
ISSUES: dict[str, tuple[str, float, float, float]] = {
    "brake_early":        ("entry", 1.00, 10.0, 0.04),
    "brake_late":         ("entry", 1.00, 10.0, 0.0),
    "lockup":             ("entry", 0.90, 10.0, 0.03),
    "apex_slow":          ("entry", 0.80, 5.0, 0.03),
    "brake_soft":         ("entry", 0.70, 0.15, 0.03),
    "brake_ramp":         ("entry", 0.50, 0.2, 0.0),
    "overrev":            ("entry", 0.60, 1.0, 0.03),
    "late_downshift":     ("entry", 0.50, 1.0, 0.0),
    "coasting":           ("both", 0.80, 20.0, 0.03),
    "throttle_late":      ("exit", 0.90, 15.0, 0.03),
    "full_throttle_late": ("exit", 0.70, 25.0, 0.0),
    "wheelspin":          ("exit", 0.80, 15.0, 0.03),
    "lift_mid":           ("exit", 0.70, 1.0, 0.0),
    "exit_slow":          ("exit", 0.50, 5.0, 0.0),
    "gear_high":          ("both", 0.60, 1.0, 0.0),
    "gear_low":           ("both", 0.35, 1.0, 0.0),
}


@dataclass
class Issue:
    kind: str
    amount: float     # metres / km/h / gear number, depending on the kind
    score: float


@dataclass
class Capability:
    """What the car has shown it can do: best lateral g and braking decel at similar speeds."""
    grip: float | None = None
    decel: float | None = None


def diagnose(m: CornerMetrics, t: CornerMetrics, c: Corner, cap: Capability | None = None,
             relative_only: bool = False) -> list[Issue]:
    """Why m is slower than t. relative_only: just the differences (used against rivals)."""
    entry_loss = max(m.t_entry - t.t_entry, 0.0)
    exit_loss = max(m.t_exit - t.t_exit, 0.0)
    found: dict[str, Issue] = {}

    def add(kind: str, amount: float) -> None:
        phase, weight, scale, base = ISSUES[kind]
        loss = {"entry": entry_loss, "exit": exit_loss}.get(phase, (entry_loss + exit_loss) / 2)
        mag = min(max(abs(amount) / scale, 0.5), 2.0)
        issue = Issue(kind, amount, (loss + base) * weight * mag)
        if kind not in found or issue.score > found[kind].score:
            found[kind] = issue

    # -- braking -----------------------------------------------------------------------
    late = False
    if m.brake_point is not None and t.brake_point is not None:
        dbp = m.brake_point - t.brake_point
        if dbp < -8 and m.v_min < t.v_min + 3:
            add("brake_early", -dbp)
        elif dbp > 8 and (m.v_min < t.v_min - 3 or m.lockup > 5 or m.t_exit - t.t_exit > 0.05):
            add("brake_late", dbp)
            late = True
    elif m.decel_point is not None and t.decel_point is not None:
        dbp = m.decel_point - t.decel_point
        if dbp < -8 and m.v_min < t.v_min + 3:
            add("brake_early", -dbp)
        elif dbp > 8 and m.v_min < t.v_min - 3:
            add("brake_late", dbp)
            late = True
    if relative_only:
        if m.v_min < t.v_min - 3 and not late:
            add("apex_slow", t.v_min - m.v_min)
        if m.throttle_on is not None and t.throttle_on is not None and m.throttle_on - t.throttle_on > 12:
            add("throttle_late", m.throttle_on - t.throttle_on)
        if m.v_exit < t.v_exit - 3:
            add("exit_slow", t.v_exit - m.v_exit)
        if m.gear_apex > 0 and t.gear_apex > 0 and m.gear_apex != t.gear_apex:
            add("gear_high" if m.gear_apex > t.gear_apex else "gear_low", t.gear_apex)
        return sorted(found.values(), key=lambda i: i.score, reverse=True)
    if m.coast_entry >= 15 and not late:
        add("brake_early", m.coast_entry)        # off the brakes and coasting into the turn
    if c.heavy and m.brake_point is not None:
        if m.brake_peak < 0.7 and m.lockup == 0:
            add("brake_soft", 0.85 - m.brake_peak)
        elif m.brake_peak < t.brake_peak - 0.15:
            add("brake_soft", t.brake_peak - m.brake_peak)
        elif cap and cap.decel and m.brake_decel and m.brake_decel < 0.78 * cap.decel and m.lockup == 0:
            add("brake_soft", 1 - m.brake_decel / cap.decel)
        if m.brake_ramp > 0.4 and m.brake_ramp > t.brake_ramp + 0.15:
            add("brake_ramp", m.brake_ramp)
    if m.lockup > 6:
        add("lockup", m.lockup)
    if m.overrev:
        add("overrev", 1)
    if m.late_downshift and not t.late_downshift:
        add("late_downshift", 1)

    # -- mid corner ----------------------------------------------------------------------
    if not late:
        if m.v_min < t.v_min - 3:
            add("apex_slow", t.v_min - m.v_min)
        if cap and cap.grip and cap.grip >= 0.6 and m.g_peak >= 0.3 and m.g_peak < 0.85 * cap.grip:
            # same radius, more grip -> v scales with sqrt(g)
            add("apex_slow", m.v_min * ((cap.grip / m.g_peak) ** 0.5 - 1))

    # -- exit ------------------------------------------------------------------------------
    coast_exit = m.coast - m.coast_entry
    if coast_exit >= 15 or (m.coast > max(12.0, t.coast + 10.0)):
        add("coasting", m.coast)
    if m.throttle_on is not None and t.throttle_on is not None and m.throttle_on - t.throttle_on > 12:
        add("throttle_late", m.throttle_on - t.throttle_on)
    if m.plateau >= 25 and m.wheelspin < 5:
        add("throttle_late", m.plateau)
    if (m.full_throttle is not None and t.full_throttle is not None
            and m.full_throttle - t.full_throttle > 25 and m.wheelspin < 5):
        add("full_throttle_late", m.full_throttle - t.full_throttle)
    if m.wheelspin > 10:
        add("wheelspin", m.wheelspin)
    if m.lift_mid and not t.lift_mid:
        add("lift_mid", 1)
    if m.v_exit < t.v_exit - 3:
        add("exit_slow", t.v_exit - m.v_exit)
    if m.gear_apex > 0 and t.gear_apex > 0 and m.gear_apex != t.gear_apex:
        add("gear_high" if m.gear_apex > t.gear_apex else "gear_low", t.gear_apex)
    return sorted(found.values(), key=lambda i: i.score, reverse=True)


@dataclass
class CornerResult:
    corner: Corner
    metrics: CornerMetrics
    target: CornerMetrics
    loss: float                      # seconds vs target (positive = slower)
    issues: list[Issue] = field(default_factory=list)
    cap: Capability | None = None
    rival: "RivalBest | None" = None           # fastest other driver here, if quicker than you
    rival_gap: float = 0.0                     # seconds slower than that rival
    rival_issues: list[Issue] = field(default_factory=list)

    @property
    def primary(self) -> Issue | None:
        return self.issues[0] if self.issues else None


@dataclass
class CornerBest:
    time: float
    metrics: CornerMetrics
    trace: Trace
    lap_id: str


@dataclass
class RivalBest(CornerBest):
    driver: str = ""


def capabilities(pairs: list[tuple[Corner, CornerMetrics]]) -> dict[int, Capability]:
    """Best grip / braking shown by any corner at a similar speed (this lap or your bests)."""
    out = {}
    for c, m in pairs:
        grips = [x.g_peak for _, x in pairs if 0.65 * m.v_min <= x.v_min <= 1.35 * m.v_min]
        decels = [x.brake_decel for k, x in pairs
                  if k.heavy and x.brake_decel and 0.7 * m.v_max <= x.v_max <= 1.3 * m.v_max]
        out[c.index] = Capability(max(grips) if grips else None, max(decels) if decels else None)
    return out


def analyze_corners(tr: Trace, model: TrackModel, best: dict[int, CornerBest],
                    rpm_limit: float) -> list[CornerResult]:
    measured = [(c, m) for c in model.corners if (m := extract(tr, c, rpm_limit)) is not None]
    pool = measured + [(c, best[c.index].metrics) for c in model.corners if c.index in best]
    caps = capabilities(pool)
    results = []
    for c, m in measured:
        b = best.get(c.index)
        target = b.metrics if b else m
        cap = caps.get(c.index)
        results.append(CornerResult(c, m, target, m.time - target.time, diagnose(m, target, c, cap), cap))
    return results

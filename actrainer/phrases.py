"""Everything the coach says, kept short enough to hear between corners."""
from __future__ import annotations

import re

ORDINAL = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth",
           7: "seventh", 8: "eighth"}


def gear(g: int) -> str:
    return ORDINAL.get(g, f"gear {g}")


def amount(seconds: float) -> str:
    a = abs(seconds)
    if a < 0.095:
        n = max(1, round(a * 100))
        return f"{n} hundredth" + ("s" if n != 1 else "")
    if a < 0.95:
        n = round(a * 10)
        return f"{n} tenth" + ("s" if n != 1 else "")
    return f"{a:.1f} seconds"


def delta(seconds: float) -> str:
    return ("plus " if seconds >= 0 else "minus ") + amount(seconds)


def lap_time(t: float) -> str:
    minutes, tenths_total = divmod(int(round(t * 10)), 600)
    whole, tenths = divmod(tenths_total, 10)
    if minutes < 1:
        return f"{whole} point {tenths}"
    secs = f"oh {whole}" if whole < 10 else str(whole)
    return f"{int(minutes)} {secs} point {tenths}"


def metres(m: float) -> str:
    n = max(5, int(round(abs(m) / 5.0)) * 5)
    return f"{n} meters"


def rpm(r: float) -> str:
    return str(int(round(r, -2)))


# said on the approach to a corner you're working on
CUE = {
    "brake_early":        "{c}, brake {m} later",
    "brake_late":         "{c}, brake a touch earlier",
    "brake_soft":         "{c}, hit the brakes harder",
    "brake_ramp":         "{c}, brake harder initially",
    "lockup":             "{c}, ease the brake, you're locking up",
    "apex_slow":          "{c}, carry more speed to the apex",
    "overrev":            "{c}, downshift a bit later",
    "late_downshift":     "{c}, finish your downshifts before turn in",
    "coasting":           "{c}, no coasting, back to the throttle",
    "throttle_late":      "{c}, earlier on the throttle",
    "full_throttle_late": "{c}, full throttle sooner",
    "wheelspin":          "{c}, smoother on the throttle",
    "lift_mid":           "{c}, commit, don't lift",
    "exit_slow":          "{c}, focus on the exit",
    "gear_high":          "{c}, use {g}",
    "gear_low":           "{c}, try {g}",
    "explore":            "{c}, try braking 5 meters later",
}

# why a corner cost time, for the lap summary
REASON = {
    "brake_early":        "braking too early",
    "brake_late":         "braking too late",
    "brake_soft":         "not enough brake pressure",
    "brake_ramp":         "too gentle on the brake",
    "lockup":             "locking up",
    "apex_slow":          "too slow at the apex",
    "overrev":            "downshifting too early",
    "late_downshift":     "late downshifts",
    "coasting":           "coasting",
    "throttle_late":      "late on the throttle",
    "full_throttle_late": "slow to full throttle",
    "wheelspin":          "wheelspin on exit",
    "lift_mid":           "lifting mid corner",
    "exit_slow":          "slow exit",
    "gear_high":          "gear too high",
    "gear_low":           "gear too low",
}

# instant verdict straight after a focus corner when the problem is still there
STILL = {
    "brake_early": "Still early on the brakes",
    "brake_late": "Still too deep",
    "coasting": "Still coasting",
    "throttle_late": "Still late on the throttle",
    "apex_slow": "Still slow at the apex",
    "lockup": "Locked up again",
    "wheelspin": "Wheelspin again",
    "brake_late": "Still too deep",
    "brake_soft": "Brake harder",
    "brake_ramp": "Hit the brake harder",
    "exit_slow": "Exit still slow",
    "full_throttle_late": "Still slow to full throttle",
    "lift_mid": "Lifted again",
    "gear_high": "Gear still too high",
    "gear_low": "Gear still too low",
    "overrev": "Over-revved again",
    "late_downshift": "Downshifts still late",
}


MAX_MOVE = 20.0   # never ask for a braking point moved further than this in one go


# what the faster rival does differently, for the lap summary
RIVAL = {
    "brake_early": "braking {m} later",
    "brake_late": "braking earlier",
    "apex_slow": "carrying more speed to the apex",
    "exit_slow": "faster out of the corner",
    "throttle_late": "on the throttle earlier",
    "gear_high": "using {g}",
    "gear_low": "using {g}",
}


def rival_reason(kind: str, amount_m: float = 0.0, gear_no: int = 0) -> str:
    return RIVAL.get(kind, "faster through there").format(m=metres(amount_m), g=gear(gear_no))


def speakable_name(name: str) -> str:
    """Online names are often full of tags and symbols - fall back to something sayable."""
    cleaned = re.sub(r"[^A-Za-z .'-]", "", name or "").strip()
    return cleaned if 2 <= len(cleaned) <= 24 else "the quickest rival"


def cue(kind: str, corner: str, amount_m: float = 0.0, gear_no: int = 0) -> str:
    return CUE[kind].format(c=corner, m=metres(min(amount_m, MAX_MOVE)), g=gear(gear_no))


def still(kind: str) -> str:
    return STILL.get(kind, "Not yet")

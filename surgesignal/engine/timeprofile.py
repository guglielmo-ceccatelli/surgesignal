"""How a disruption's demand effect rises and falls over time.

    g(t)
    1.0 ┤      ┌──────────────────┐
        │     /                    \
        │    /                      `-._  half-life decay
        │   /                            `--.__
    0.0 ┼──┴──────────────────────┴──────────────── t
          t0  t0+ramp        plateau end (resolved_at, or t0 + expected duration)

While a disruption is active, the expected duration keeps stretching:
    dur = max(default, elapsed + default / 2)
so an active disruption always stays on the plateau (after the ramp).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from surgesignal.engine.params import Params
from surgesignal.engine.types import Disruption


def minutes_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0


def expected_duration_min(d: Disruption, now: datetime, p: Params) -> float:
    """Plateau length in minutes from t0."""
    if d.resolved_at is not None:
        return max(0.0, minutes_between(d.t0, d.resolved_at))
    default = d.expected_duration_min if d.expected_duration_min is not None else p.default_duration_min(d.severity_class)
    elapsed = max(0.0, minutes_between(d.t0, now))
    return max(default, elapsed + default / 2)


def expected_end(d: Disruption, now: datetime, p: Params) -> datetime:
    """'Until at least ...' time shown on alerts."""
    return d.t0 + timedelta(minutes=expected_duration_min(d, now, p))


def g(d: Disruption, now: datetime, p: Params) -> float:
    """Time profile in [0, 1]."""
    m = minutes_between(d.t0, now)
    if m < 0:
        return 0.0
    ramp = min(1.0, m / p.ramp_min) if p.ramp_min > 0 else 1.0
    dur = expected_duration_min(d, now, p)
    if m <= dur:
        return ramp
    # Decay starts from wherever the ramp had reached when the plateau ended.
    ramp_at_end = min(1.0, dur / p.ramp_min) if p.ramp_min > 0 else 1.0
    return ramp_at_end * 0.5 ** ((m - dur) / p.decay_half_life_min)

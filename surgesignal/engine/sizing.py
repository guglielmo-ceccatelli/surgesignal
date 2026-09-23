"""How many idle cars to move, and where.

    cars = clamp(round_half_up(k × mean pct.mid of top stations × idle_drivers), 1, idle_drivers)
    split across the top stations in proportion to `extra` (largest-remainder, sums exactly).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from surgesignal.engine.params import Params
from surgesignal.engine.types import Hotspot


def round_half_up(x: float) -> int:
    # Python's round() is banker's rounding (round(2.5) == 2); dispatch advice shouldn't be.
    return math.floor(x + 0.5)


def cars_to_move(top: Sequence[Hotspot], idle_drivers: int | None, p: Params) -> int | None:
    """None → idle_drivers not set, alert says "raise coverage" with no number."""
    if idle_drivers is None:
        return None
    if idle_drivers <= 0 or not top:
        return 0
    pct_mid = sum(h.pct.mid for h in top) / len(top)
    if pct_mid <= 0:
        return 0
    return max(1, min(idle_drivers, round_half_up(p.sizing_k * pct_mid * idle_drivers)))


def allocate(top: Sequence[Hotspot], cars: int) -> list[int]:
    """Split `cars` across stations proportionally to extra. Always sums to `cars`."""
    if cars <= 0 or not top:
        return [0] * len(top)
    weights = [max(h.extra, 0.0) for h in top]
    total = sum(weights)
    if total == 0:
        weights, total = [1.0] * len(top), float(len(top))
    shares = [cars * w / total for w in weights]
    counts = [math.floor(s) for s in shares]
    # Hand out the remainder to the largest fractional parts; ties go to the higher-ranked station.
    order = sorted(range(len(top)), key=lambda i: (-(shares[i] - counts[i]), i))
    for i in order[: cars - sum(counts)]:
        counts[i] += 1
    return counts

"""Alert text for the dispatcher. Pure: engine output in, message text out.

    ⚠️ Northern line suspended (unplanned, 18:07).
    Expect +45–86% booking demand around Stockwell, Clapham Common, Balham until at least ~19:37.
    Why: Northern line suspended (unplanned) + heavy rain + Friday peak
    Suggest: move 2 of your 6 idle cars to wait nearby (not at the station): Stockwell 1, Clapham Common 1.

Times are shown in London time. Private-hire cars can't wait at stations for passengers
(pre-booked only), hence "nearby".
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from surgesignal.engine import sizing
from surgesignal.engine.explain import SEVERITY_WORDS
from surgesignal.engine.params import Params
from surgesignal.engine.timeprofile import expected_end
from surgesignal.engine.types import Disruption, EngineResult, Hotspot

LONDON = ZoneInfo("Europe/London")


def london_hhmm(t: datetime) -> str:
    return t.astimezone(LONDON).strftime("%H:%M")


def pct_range(h: Hotspot) -> str:
    return f"+{round(h.pct.low * 100)}–{round(h.pct.high * 100)}%"


def headline(d: Disruption) -> str:
    kind = "unplanned" if d.is_unplanned else "planned"
    return f"{d.line_name} line {SEVERITY_WORDS[d.severity_class]} ({kind}, {london_hhmm(d.t0)})"


def format_alert(d: Disruption, top: Sequence[Hotspot], idle_drivers: int | None, now: datetime, p: Params,
                 area_uncertain: bool = False) -> str:
    """Message for one disruption and its top in-area stations."""
    if not top:
        return f"⚠️ {headline(d)}. No stations in your area are affected."
    lo = min(round(h.pct.low * 100) for h in top)
    hi = max(round(h.pct.high * 100) for h in top)
    names = ", ".join(h.station.name for h in top)
    lines = [
        f"⚠️ {headline(d)}.",
        f"Expect +{lo}–{hi}% booking demand around {names} until at least ~{london_hhmm(expected_end(d, now, p))}.",
        f"Why: {top[0].explanation}",
    ]
    if area_uncertain:
        lines.append("Area uncertain: TfL's description couldn't be matched to exact stations, so this covers the whole line at half strength.")
    cars = sizing.cars_to_move(top, idle_drivers, p)
    if cars is None:
        lines.append("Suggest: raise coverage nearby (set your idle cars in /settings for a number).")
    elif cars > 0:
        split = ", ".join(f"{h.station.name} {n}" for h, n in zip(top, sizing.allocate(top, cars)) if n)
        lines.append(f"Suggest: move {cars} of your {idle_drivers} idle cars to wait nearby (not at the station): {split}.")
    return "\n".join(lines)


def format_line_wide(d: Disruption) -> str:
    return f"ℹ️ {headline(d)} across the line. TfL gave no section, so no station suggestion."


def top_for(result: EngineResult, d: Disruption, p: Params) -> list[Hotspot]:
    """Top in-area stations where this disruption is a cause (highest extra first)."""
    return [h for h in result.hotspots if d.key in h.disruption_keys][: p.top_n]

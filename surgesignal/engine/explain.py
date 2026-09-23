"""One plain-English line per hotspot, built from its largest contributions.

    "Northern line suspended (unplanned) + heavy rain + Friday peak"

Parts are ordered by size of effect (disruption terms and multiplier boosts compared on the
same fraction-of-baseline scale), at most 3 factor parts, then the time label if room.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from surgesignal.engine.params import Params
from surgesignal.engine.types import Conditions, Disruption, Station

SEVERITY_WORDS = {
    "suspended": "suspended",
    "part_suspended": "part suspended",
    "severe": "severe delays",
    "minor": "minor delays",
}
MAX_PARTS = 3
# Contributions below this (fraction of baseline) are left out of explanations and of a
# hotspot's disruption_keys: a section 20 km away adds ~e^-25, which is noise, not a cause.
NEGLIGIBLE_UPLIFT = 0.005


def line_label(line_name: str) -> str:
    """'Northern' → 'Northern line'; TfL's 'Elizabeth line' already says it (no 'line line')."""
    return line_name if line_name.lower().endswith(" line") else f"{line_name} line"


def disruption_phrase(d: Disruption) -> str:
    kind = "unplanned" if d.is_unplanned else "planned"
    return f"{line_label(d.line_name)} {SEVERITY_WORDS[d.severity_class]} ({kind})"


def weather_phrase(c: Conditions, p: Params) -> str | None:
    parts = []
    if c.precip_mm_h >= p.heavy_rain_threshold_mm_h:
        parts.append("heavy rain")
    elif c.precip_mm_h >= p.dry_threshold_mm_h:
        parts.append("light rain")
    if c.temp_c < p.cold_threshold_c:
        parts.append("cold")
    return " + ".join(parts) or None


def explain(station: Station, disruptions: Sequence[Disruption], terms: Mapping[str, float],
            c: Conditions, w_mult: float, e_mult: float, p: Params) -> str:
    # Merge by phrase: two sections of the same line with the same severity read as one cause.
    by_phrase: dict[str, float] = {}
    for d in disruptions:
        term = terms.get(d.key, 0.0)
        if term >= NEGLIGIBLE_UPLIFT:
            by_phrase[disruption_phrase(d)] = by_phrase.get(disruption_phrase(d), 0.0) + term
    scored: list[tuple[float, str]] = [(v, k) for k, v in by_phrase.items()]
    wp = weather_phrase(c, p)
    if wp and w_mult > 1:
        scored.append((w_mult - 1, wp))
    if e_mult > 1:
        scored.append((e_mult - 1, "event ending nearby"))

    scored.sort(key=lambda x: -x[0])
    parts = [text for _, text in scored[:MAX_PARTS]]
    if c.time_label and len(parts) < MAX_PARTS + 1:
        parts.append(c.time_label)
    return " + ".join(parts) if parts else f"Normal conditions at {station.name}"

"""The factor engine: pure function from (disruptions, conditions, params) to ranked hotspots.

    uplift(s) = Σ_d  sev(d) · plan_mult(d) · scale(d) · exp(−dist(s,d)/λ) · g(d, now)
    mult(s)   = weather_mult × event_mult(s)
    pct(s)    = (1 + uplift) × mult − 1             ← shown to the dispatcher
    extra(s)  = baseline(s) × uplift.mid × mult     ← ranking + car allocation: demand the DISRUPTION
                                                      adds (amplified by weather/events). Rain alone
                                                      raises pct everywhere but never makes a hotspot.

    Ranges: low = sev×0.5, λ×0.5 · mid = nominal · high = sev×1.5, λ×1.5  (from params)

    disruptions ─┬─ line_wide ────────────► result.line_wide (never ranked, OV#2)
                 └─ sectional ─► uplift ─┐
    conditions ──► weather, events ──────┴─► pct, extra ─► in service area? ─► rank by extra

No network, database or clock: `now` and `baseline` are passed in, so the what-if
simulator and the live alerter run exactly the same code (eng review D1).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime

from surgesignal.engine.explain import NEGLIGIBLE_UPLIFT, explain
from surgesignal.engine.geo import distance_to_section, haversine_km, in_service_area
from surgesignal.engine.params import Params
from surgesignal.engine.timeprofile import g, minutes_between
from surgesignal.engine.types import (
    Conditions,
    Disruption,
    EngineResult,
    Hotspot,
    Range,
    ServiceArea,
    Station,
)


def disruption_term(d: Disruption, station: Station, stations: Mapping[str, Station], now: datetime,
                    p: Params, sev_factor: float = 1.0, lambda_factor: float = 1.0) -> float:
    """One disruption's contribution to one station's uplift (fraction of baseline)."""
    if d.line_wide:
        return 0.0
    sev = p.severity(d.severity_class) * sev_factor * d.severity_scale
    sev *= p.unplanned_multiplier if d.is_unplanned else p.planned_multiplier
    dist = distance_to_section(station, d.affected_station_ids, stations)
    return sev * math.exp(-dist / (p.lambda_km * lambda_factor)) * g(d, now, p)


def uplift(station: Station, disruptions: Iterable[Disruption], stations: Mapping[str, Station],
           now: datetime, p: Params, sev_factor: float = 1.0, lambda_factor: float = 1.0) -> float:
    return sum(disruption_term(d, station, stations, now, p, sev_factor, lambda_factor) for d in disruptions)


def weather_mult(c: Conditions, p: Params) -> float:
    if c.precip_mm_h < p.dry_threshold_mm_h:
        m = 1.0
    elif c.precip_mm_h < p.heavy_rain_threshold_mm_h:
        m = p.light_rain_multiplier
    else:
        m = p.heavy_rain_multiplier
    if c.temp_c < p.cold_threshold_c:
        m += p.cold_bonus
    return m


def event_mult(station: Station, c: Conditions, now: datetime, p: Params) -> float:
    """Largest multiplier among events ending nearby within the window (not a product)."""
    best = 1.0
    for e in c.events:
        ends_in = minutes_between(now, e.ends_at)
        if not 0 <= ends_in <= p.event_window_min:
            continue
        if haversine_km(station.lat, station.lon, e.lat, e.lon) > p.event_radius_km:
            continue
        if e.capacity >= p.event_large_capacity:
            best = max(best, p.event_large_multiplier)
        elif e.capacity >= p.event_medium_capacity:
            best = max(best, p.event_medium_multiplier)
    return best


def run(stations: Mapping[str, Station], disruptions: Iterable[Disruption], conditions: Conditions,
        baseline: Mapping[str, float], area: ServiceArea, now: datetime, p: Params) -> EngineResult:
    """Score every station in the service area.

    baseline: relative demand per station id at `now` (synthetic, labelled; see inputs/baseline).
    Stations missing from baseline are scored with baseline 0, so they can show a pct but never rank.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware (UTC)")
    disruptions = tuple(disruptions)
    sectional = tuple(d for d in disruptions if not d.line_wide)
    w_mult = weather_mult(conditions, p)

    hotspots = []
    for station in stations.values():
        if not in_service_area(station, area):
            continue
        u = Range(
            low=uplift(station, sectional, stations, now, p, p.range_low_factor, p.range_low_factor),
            mid=uplift(station, sectional, stations, now, p),
            high=uplift(station, sectional, stations, now, p, p.range_high_factor, p.range_high_factor),
        )
        mult = w_mult * event_mult(station, conditions, now, p)
        pct = Range(low=(1 + u.low) * mult - 1, mid=(1 + u.mid) * mult - 1, high=(1 + u.high) * mult - 1)
        if pct.mid <= 0:
            continue
        terms = {d.key: disruption_term(d, station, stations, now, p) for d in sectional}
        contributing = tuple(k for k, v in sorted(terms.items(), key=lambda kv: -kv[1]) if v >= NEGLIGIBLE_UPLIFT)
        hotspots.append(Hotspot(
            station=station,
            uplift=u,
            pct=pct,
            mult=mult,
            extra=baseline.get(station.id, 0.0) * u.mid * mult,
            explanation=explain(station, sectional, terms, conditions, w_mult, mult / w_mult, p),
            disruption_keys=contributing,
        ))

    hotspots.sort(key=lambda h: (-h.extra, h.station.name))
    return EngineResult(
        hotspots=tuple(hotspots),
        line_wide=tuple(d for d in disruptions if d.line_wide),
        area_uncertain=tuple(d for d in disruptions if d.area_uncertain),
    )

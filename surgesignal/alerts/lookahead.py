"""Look-ahead: planned closures, strikes and big events in the next few days, and where to have
cars. Sent daily (default 18:00 London) and on /week. Pure: data in, items and text out.

    planned incidents (TfL future status) ──► each scheduled period overlapping the horizon
        └─► the same engine, every STEP through the period ─► busiest moment in the area
            (top stations by riders the closure adds) ─► item if it adds ≥ lookahead_min_uplift
    events.csv ──► stations in the area within event_radius_km of the venue
        └─► event multiplier at the end time, ranked by baseline ─► item

Closures are scored as planned (riders re-route in advance: planned_multiplier). A strike is
reported the same way, labelled as a strike.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from surgesignal.alerts.format import LONDON, unique_places
from surgesignal.engine import model, sizing
from surgesignal.engine.explain import SEVERITY_WORDS, line_label
from surgesignal.engine.geo import haversine_km, in_service_area
from surgesignal.engine.params import Params
from surgesignal.engine.types import Conditions, Disruption, Event, Hotspot, Range, ServiceArea, Station
from surgesignal.tfl.parser import SEVERITY_RANK, Incident, section_matches

STEP = timedelta(minutes=30)
Baseline = Callable[[datetime], Mapping[str, float]]


@dataclass(frozen=True)
class Item:
    starts: datetime
    ends: datetime
    title: str
    peak_at: datetime
    top: tuple[Hotspot, ...]
    kind: str  # "closure" | "strike" | "event"


def _sections(reason: str) -> str | None:
    spans = [f"between {m.group('span')}" for m in section_matches(reason)]
    if not spans:
        return None
    return "; ".join(spans[:2]) + (f" (+{len(spans) - 2} more)" if len(spans) > 2 else "")


def _group(incidents: Sequence[Incident], line_stations: Mapping[str, frozenset[str]]) -> list[Incident]:
    """One planned closure per TfL status: TfL lists a weekend closure's sections as separate clauses,
    which the parser turns into separate incidents. Merge them back: union of stations, worst severity."""
    groups: dict[tuple, list[Incident]] = {}
    for inc in incidents:
        if not inc.is_unplanned:
            groups.setdefault((inc.line, inc.reason, inc.periods), []).append(inc)
    merged = []
    for (line, _, _), incs in groups.items():
        stations = set()
        for inc in incs:
            stations |= set(inc.station_ids) or set(line_stations.get(line, ()))  # line-wide → whole line
        worst = max(incs, key=lambda i: SEVERITY_RANK[i.severity_class])
        merged.append(replace(worst, station_ids=tuple(sorted(stations)), line_wide=False))
    return merged


def closure_items(incidents: Sequence[Incident], stations: Mapping[str, Station], line_stations: Mapping[str, frozenset[str]],
                  baseline: Baseline, area: ServiceArea, now: datetime, p: Params) -> list[Item]:
    """Planned incidents only: live ones are the alerter's job; the look-ahead is for what's scheduled."""
    horizon = now + timedelta(days=p.lookahead_days)
    items = []
    for inc in _group(incidents, line_stations):
        if not inc.station_ids:
            continue
        strike = "strike" in inc.reason.lower()
        for start, end in inc.periods:
            lo, hi = max(start, now), min(end, horizon)
            if lo >= hi:
                continue
            d = Disruption(key=inc.key, line=inc.line, line_name=inc.line_name, severity_class=inc.severity_class,
                           is_unplanned=False, affected_station_ids=inc.station_ids, t0=start, resolved_at=end)
            best: tuple[float, datetime, tuple[Hotspot, ...]] | None = None
            t = lo + min(timedelta(minutes=p.ramp_min), (hi - lo) / 2)  # past the build-up
            while t < hi:
                result = model.run(stations, [d], Conditions(), baseline(t), area, t, p)
                top = tuple(unique_places(result.hotspots, p.top_n))
                score = sum(h.extra for h in top)
                if top and (best is None or score > best[0]):
                    best = (score, t, top)
                t += STEP
            if best is None or max(h.uplift.mid for h in best[2]) < p.lookahead_min_uplift:
                continue
            sections = _sections(inc.reason)
            title = (f"{line_label(inc.line_name)} {SEVERITY_WORDS[inc.severity_class]} "
                     f"({'strike' if strike else 'planned'})" + (f" {sections}" if sections else ""))
            items.append(Item(start, end, title, best[1], best[2], "strike" if strike else "closure"))
    return items


def event_items(events: Sequence[Event], stations: Mapping[str, Station], baseline: Baseline,
                area: ServiceArea, now: datetime, p: Params) -> list[Item]:
    horizon = now + timedelta(days=p.lookahead_days)
    items = []
    for e in events:
        if not now <= e.ends_at <= horizon:
            continue
        if e.capacity >= p.event_large_capacity:
            mult = p.event_large_multiplier
        elif e.capacity >= p.event_medium_capacity:
            mult = p.event_medium_multiplier
        else:
            continue
        near = [s for s in stations.values()
                if in_service_area(s, area) and haversine_km(s.lat, s.lon, e.lat, e.lon) <= p.event_radius_km]
        if not near:
            continue
        busy = baseline(e.ends_at)
        pct = Range(mult - 1, mult - 1, mult - 1)
        hs = sorted((Hotspot(s, Range(0, 0, 0), pct, mult, busy.get(s.id, 0.0) * (mult - 1), "event") for s in near),
                    key=lambda h: (-h.extra, h.station.name))
        items.append(Item(e.ends_at - timedelta(minutes=p.event_before_min), e.ends_at + timedelta(minutes=p.event_after_min),
                          f"{e.name} ends ({e.capacity:,} capacity)", e.ends_at, tuple(unique_places(hs, p.top_n)), "event"))
    return items


def _day_time(t: datetime) -> str:
    return t.astimezone(LONDON).strftime("%a %d %b %H:%M")


def _hhmm(t: datetime) -> str:
    return t.astimezone(LONDON).strftime("%H:%M")


def format_lookahead(items: Sequence[Item], area_label: str, radius_km: float, idle_drivers: int | None,
                     days: float, p: Params) -> str:
    head = f"📅 Next {days:g} days near {area_label} ({radius_km:g} km)"
    if not items:
        return f"{head}: nothing planned that should move your demand."
    lines = [head + ":"]
    for it in sorted(items, key=lambda i: i.starts):
        lo = min(round(h.pct.low * 100) for h in it.top)
        hi = max(round(h.pct.high * 100) for h in it.top)
        pct = f"+{lo}%" if lo == hi else f"+{lo}–{hi}%"
        names = ", ".join(h.station.name for h in it.top)
        if it.kind == "event":
            when = f"{_day_time(it.peak_at)}: {it.title}. {_hhmm(it.starts)}–{_hhmm(it.ends)}: {pct} around {names}."
        else:
            span = f"{_day_time(it.starts)} – {_day_time(it.ends)}"
            when = f"{span}: {it.title}. Busiest around {_day_time(it.peak_at)}: {pct} around {names}."
        cars = sizing.cars_to_move(it.top, idle_drivers, p)
        if cars:
            when += f" Have {cars} of your {idle_drivers} cars nearby then."
        lines.append("• " + when)
    return "\n".join(lines)

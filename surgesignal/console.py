"""Logic behind the Streamlit console (console/app.py). No Streamlit here, so it's all testable.

    whatif()      a hypothetical disruption, run through the SAME engine as live alerts at several
                  minutes after it starts ─► map frames (the ripple) + the Telegram alert it would send
    live_view()   the alerter's stored state (tracker, settings, check-ins) ─► current hotspots
    params_rows() params.yaml as a table for "How it works"
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from surgesignal.alerts.format import format_alert, top_for, unique_places
from surgesignal.alerts.settings import Settings
from surgesignal.engine import model
from surgesignal.engine.params import Params, load_raw
from surgesignal.engine.types import Conditions, Disruption, EngineResult, ServiceArea, Station
from surgesignal.inputs.baseline import Baseline
from surgesignal.store.state import Checkin, Store
from surgesignal.tfl.network import Network
from surgesignal.tfl.parser import section_between
from surgesignal.tfl.tracker import IncidentTracker

WHATIF_STEPS = (8, 15, 30, 60)  # minutes after the disruption starts (8 = demand has built up)
RAIN = {"Dry": 0.0, "Light rain": 1.0, "Heavy rain": 3.0}
MIN_PCT_SHOWN = 0.01


def ordered_stations(network: Network, line_id: str) -> list[Station]:
    """A line's stations in running order (longest route first, then branch stations it lacks)."""
    seen: dict[str, None] = {}
    for route in sorted(network.lines[line_id].routes, key=lambda r: -len(r.station_ids)):
        for sid in route.station_ids:
            seen.setdefault(sid, None)
    return [network.stations[sid] for sid in seen]


@dataclass(frozen=True)
class WhatIf:
    frames: list[dict[str, Any]]  # one row per (minutes, station) shown on the map
    alert: str  # the Telegram message the dispatcher would get
    section: tuple[str, ...]


def whatif(network: Network, params: Params, baseline: Baseline, line_id: str, a: str, b: str, severity: str,
           unplanned: bool, start: datetime, rain: str, area: ServiceArea, idle: int | None,
           steps: Sequence[int] = WHATIF_STEPS) -> WhatIf:
    section = section_between(network, line_id, a, b)
    if not section:
        raise ValueError("those two stations aren't on one route of this line")
    line = network.lines[line_id]
    d = Disruption(key=f"whatif:{line_id}", line=line_id, line_name=line.name, severity_class=severity,
                   is_unplanned=unplanned, affected_station_ids=tuple(sorted(section)), t0=start)
    frames: list[dict[str, Any]] = []
    alert_result: EngineResult | None = None
    for minutes in steps:
        t = start + timedelta(minutes=minutes)
        conditions = Conditions(precip_mm_h=RAIN[rain], time_label=baseline.time_label(t))
        result = model.run(network.stations, [d], conditions, baseline.at(t), area, t, params)
        if alert_result is None:
            alert_result = result
        for h in result.hotspots:
            if h.pct.mid < MIN_PCT_SHOWN:
                continue
            frames.append({"minutes": f"+{minutes} min", "station": h.station.name, "lat": h.station.lat,
                           "lon": h.station.lon, "demand_pct": round(h.pct.mid * 100, 1),
                           "low_pct": round(h.pct.low * 100), "high_pct": round(h.pct.high * 100),
                           "extra_riders": round(h.extra, 3), "in_section": h.station.id in section,
                           "why": h.explanation})
    top = top_for(alert_result, d, params) if alert_result else []
    return WhatIf(frames, format_alert(d, top, idle, start + timedelta(minutes=steps[0]), params), tuple(sorted(section)))


@dataclass(frozen=True)
class LiveView:
    settings: Settings
    disruptions: list[Disruption]
    hotspots: list[dict[str, Any]]
    checkins: list[Checkin]


def live_view(store: Store, network: Network, params: Params, baseline: Baseline, now: datetime) -> LiveView:
    settings = Settings.from_dict(store.get_state("settings"))
    tracker = IncidentTracker.from_dict(params, store.get_state("tracker") or {})
    disruptions = [d for d in tracker.disruptions() if d.resolved_at is None]
    checkins = store.checkins_since(now - timedelta(minutes=30))
    rows: list[dict[str, Any]] = []
    if settings.has_area and disruptions:
        at = now + timedelta(minutes=params.ramp_min)
        area = ServiceArea(settings.area_lat, settings.area_lon, settings.radius_km)
        result = model.run(network.stations, disruptions, Conditions(time_label=baseline.time_label(at)),
                           baseline.at(at), area, at, params)
        for h in unique_places((h for h in result.hotspots if h.disruption_keys), 10):
            rows.append({"station": h.station.name, "lat": h.station.lat, "lon": h.station.lon,
                         "demand": f"+{round(h.pct.low * 100)}–{round(h.pct.high * 100)}%",
                         "demand_pct": round(h.pct.mid * 100, 1), "extra_riders": round(h.extra, 3), "why": h.explanation})
    return LiveView(settings, disruptions, rows, checkins)


def params_rows() -> list[dict[str, Any]]:
    return [{"parameter": name, "value": e["value"], "unit": e["unit"], "source": e["source"], "note": e.get("note", "")}
            for name, e in load_raw().items()]

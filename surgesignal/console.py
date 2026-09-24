"""Logic behind the Streamlit console (console/app.py). No Streamlit here, so it's all testable.

    ripple()        any disruption(s) ─► the SAME engine as live alerts at +8/15/30/60 min
                    ─► map frames, the drawn section, the action card, the exact alert text
    replay_story()  a real recorded disruption (committed snapshots) ─► the alerts actually
                    generated + its ripple            (the Overview: works deployed, no live data)
    whatif()        a hypothetical section on a line ─► ripple()          (Try a disruption)
    live_view()     the alerter's stored state + heartbeat ─► the dispatcher's current view
    PRESETS         fixed-date demo scenarios, deep-linkable with ?scenario=
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from surgesignal.alerts.format import LONDON, format_alert, london_hhmm, top_for
from surgesignal.alerts.settings import Settings
from surgesignal.engine import model, sizing
from surgesignal.engine.explain import SEVERITY_WORDS, line_label
from surgesignal.engine.params import Params, load_raw
from surgesignal.engine.timeprofile import expected_end
from surgesignal.engine.types import Conditions, Disruption, Hotspot, ServiceArea, Station
from surgesignal.inputs.baseline import Baseline
from surgesignal.replay import load_snapshots, replay
from surgesignal.store.state import Checkin, Store
from surgesignal.tfl.network import Network
from surgesignal.tfl.parser import parse_status, section_between
from surgesignal.tfl.tracker import IncidentTracker

RIPPLE_STEPS = (8, 15, 30, 60)  # minutes after the disruption starts (8 = demand has built up)
RAIN = {"Dry": 0.0, "Light rain": 1.0, "Heavy rain": 3.0}
MIN_PCT_SHOWN = 0.01
STALE_AFTER = timedelta(minutes=3)
REPLAYS_DIR = Path(__file__).resolve().parent / "data" / "replays"


# ---- shared: the ripple ----------------------------------------------------------------


def ordered_stations(network: Network, line_id: str) -> list[Station]:
    """A line's stations in running order (longest route first, then branch stations it lacks)."""
    seen: dict[str, None] = {}
    for route in sorted(network.lines[line_id].routes, key=lambda r: -len(r.station_ids)):
        for sid in route.station_ids:
            seen.setdefault(sid, None)
    return [network.stations[sid] for sid in seen]


def section_paths(network: Network, line_id: str, section: Sequence[str]) -> list[list[Station]]:
    """The disrupted section as polylines in running order, one per route stretch it covers
    (a section spanning two branches draws as two lines, not a zig-zag)."""
    wanted = set(section)
    runs: list[tuple[str, ...]] = []
    for route in network.lines[line_id].routes:
        run: list[str] = []
        for sid in route.station_ids + ("",):
            if sid in wanted:
                run.append(sid)
            elif run:
                if len(run) > 1 and tuple(run) not in runs and tuple(reversed(run)) not in runs:
                    runs.append(tuple(run))
                run = []
    # drop runs contained in a longer run
    runs = [r for r in runs if not any(set(r) < set(o) for o in runs)]
    return [[network.stations[s] for s in r] for r in runs]


@dataclass(frozen=True)
class Action:
    cars: int | None  # None: idle cars not set
    idle: int | None
    split: list[tuple[str, int]]  # (station, cars)
    stations: list[str]
    pct_low: int
    pct_high: int
    until: str  # London HH:MM
    why: str
    alerting: bool = True  # False: demand moves, but below the alert threshold (the bot stays silent)


@dataclass(frozen=True)
class Ripple:
    frames: list[dict[str, Any]]  # one row per (minutes, station) for the maps
    paths: list[list[Station]]  # the disrupted section(s), for drawing
    section: tuple[str, ...]
    alert: str  # the Telegram message text
    action: Action | None  # None when nothing in the area is affected
    caption: str  # top stations as text: the non-colour channel
    area: ServiceArea


def _action(d: Disruption, top: Sequence[Hotspot], idle: int | None, at: datetime, p: Params) -> Action | None:
    if not top:
        return None
    cars = sizing.cars_to_move(top, idle, p)
    split = [(h.station.name, n) for h, n in zip(top, sizing.allocate(top, cars or 0)) if n]
    return Action(cars, idle, split, [h.station.name for h in top],
                  min(round(h.pct.low * 100) for h in top), max(round(h.pct.high * 100) for h in top),
                  london_hhmm(expected_end(d, at, p)), top[0].explanation,
                  alerting=any(h.uplift.mid >= p.alert_threshold_uplift for h in top))


def ripple(network: Network, params: Params, baseline: Baseline, disruptions: Sequence[Disruption], start: datetime,
           rain: str, area: ServiceArea, idle: int | None, steps: Sequence[int] = RIPPLE_STEPS) -> Ripple:
    lead = disruptions[0]
    section = tuple(sorted({s for d in disruptions for s in d.affected_station_ids}))
    paths = [p for d in disruptions if not d.line_wide for p in section_paths(network, d.line, d.affected_station_ids)]
    frames: list[dict[str, Any]] = []
    first_top: list[Hotspot] = []
    first_at = start
    for i, minutes in enumerate(steps):
        t = start + timedelta(minutes=minutes)
        conditions = Conditions(precip_mm_h=RAIN[rain], time_label=baseline.time_label(t))
        result = model.run(network.stations, disruptions, conditions, baseline.at(t), area, t, params)
        if i == 0:
            first_top, first_at = top_for(result, lead, params), t
        for h in result.hotspots:
            if h.pct.mid < MIN_PCT_SHOWN or not h.disruption_keys:
                continue
            frames.append({"minutes": f"+{minutes} min", "station": h.station.name, "lat": h.station.lat,
                           "lon": h.station.lon, "demand_pct": round(h.pct.mid * 100, 1),
                           "low_pct": round(h.pct.low * 100), "high_pct": round(h.pct.high * 100),
                           "extra_riders": round(h.extra, 3), "in_section": h.station.id in section,
                           "why": h.explanation})
    action = _action(lead, first_top, idle, first_at, params)
    caption = ("Busiest at +{m} min: ".format(m=steps[0]) + ", ".join(
        f"{h.station.name} +{round(h.pct.low * 100)}–{round(h.pct.high * 100)}%" for h in first_top)) if first_top else \
        "This disruption doesn't reach the chosen area."
    return Ripple(frames, paths, section, format_alert(lead, first_top, idle, first_at, params), action, caption, area)


# ---- Try a disruption ------------------------------------------------------------------


def whatif(network: Network, params: Params, baseline: Baseline, line_id: str, a: str, b: str, severity: str,
           unplanned: bool, start: datetime, rain: str, area: ServiceArea, idle: int | None,
           steps: Sequence[int] = RIPPLE_STEPS) -> Ripple:
    section = section_between(network, line_id, a, b)
    if not section:
        raise ValueError("those two stations aren't on one route of this line")
    line = network.lines[line_id]
    d = Disruption(key=f"whatif:{line_id}", line=line_id, line_name=line.name, severity_class=severity,
                   is_unplanned=unplanned, affected_station_ids=tuple(sorted(section)), t0=start)
    return ripple(network, params, baseline, [d], start, rain, area, idle, steps)


@dataclass(frozen=True)
class Preset:
    slug: str
    label: str
    line: str
    a: str  # station names
    b: str
    severity: str
    unplanned: bool
    start: datetime
    rain: str
    centre: str
    radius_km: float
    idle: int


PRESETS: dict[str, Preset] = {p.slug: p for p in (
    Preset("northern-rain", "Northern line signal failure · Fri 18:00 · rain", "northern", "Stockwell", "Morden",
           "suspended", True, datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc), "Heavy rain", "Clapham Common", 5.0, 6),
    Preset("central-weekend", "Central line weekend closure · Sat 14:00", "central", "Liverpool Street", "Woodford",
           "part_suspended", False, datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc), "Dry", "Stratford (London)", 6.0, 6),
    Preset("district-evening", "District line severe delays · Wed 19:45", "district", "Earl's Court", "Wimbledon",
           "severe", True, datetime(2026, 9, 23, 18, 45, tzinfo=timezone.utc), "Dry", "Putney Bridge", 5.0, 6),
)}


def station_by_name(network: Network, name: str, line_id: str | None = None) -> Station:
    pool = network.lines[line_id].station_ids if line_id else network.stations.keys()
    matches = sorted((network.stations[s] for s in pool if network.stations[s].name == name), key=lambda s: s.id)
    if not matches:
        raise KeyError(name)
    return matches[0]


def run_preset(network: Network, params: Params, baseline: Baseline, preset: Preset) -> Ripple:
    c = station_by_name(network, preset.centre)
    return whatif(network, params, baseline, preset.line, station_by_name(network, preset.a, preset.line).id,
                  station_by_name(network, preset.b, preset.line).id, preset.severity, preset.unplanned,
                  preset.start, preset.rain, ServiceArea(c.lat, c.lon, preset.radius_km), preset.idle)


# ---- Overview: a real recorded disruption ----------------------------------------------


@dataclass(frozen=True)
class ReplayStory:
    title: str
    recorded: str  # "23 Sep 2026, 19:45–21:46"
    started_london: str
    sent: list[tuple[str, str, str]]  # (London HH:MM, kind, text) as the alerter generated them
    ripple: Ripple | None
    area_label: str
    tfl_reason: str


def replay_story(network: Network, params: Params, baseline: Baseline, snapshots_dir: Path, line_id: str,
                 area_label: str, area: ServiceArea, idle: int) -> ReplayStory:
    """Replay the committed snapshots through the real alerter, and draw the ripple of the
    incident it alerted on first, from the moment TfL reported it."""
    snapshots = load_snapshots(snapshots_dir)
    if not snapshots:
        raise FileNotFoundError(f"no snapshots in {snapshots_dir}")
    settings = Settings(idle_drivers=idle, area_lat=area.lat, area_lon=area.lon, area_label=area_label,
                        radius_km=area.radius_km, lookahead_at=None)
    result = replay(snapshots, network, params, settings)
    sent = [(london_hhmm(e.at), e.kind, e.text) for e in result.events if e.kind in ("new", "edit", "resolved")]

    # rebuild the incident exactly as the tracker saw it at the first alert
    first_new = next((e for e in result.events if e.kind == "new"), None)
    tracker = IncidentTracker(params)
    disruptions: list[Disruption] = []
    reason = ""
    for at, raw in snapshots:
        if first_new and at > first_new.at:
            break
        parsed = parse_status(json.loads(raw), network)
        disruptions = tracker.update(parsed.incidents, at)
        reason = next((i.reason for i in parsed.incidents if i.line == line_id), reason)
    on_line = [d for d in disruptions if d.line == line_id and d.resolved_at is None and not d.line_wide]
    rp = None
    if on_line:
        start = min(d.t0 for d in on_line)
        rp = ripple(network, params, baseline, on_line, start, "Dry", area, idle)
    first, last = snapshots[0][0], snapshots[-1][0]
    lead = on_line[0] if on_line else None
    title = (f"{line_label(lead.line_name)} {SEVERITY_WORDS[lead.severity_class]}" if lead else line_id.title())
    return ReplayStory(title, f"{first.astimezone(LONDON):%d %b %Y}, {london_hhmm(first)}–{london_hhmm(last)}",
                       london_hhmm(min(d.t0 for d in on_line)) if on_line else "", sent, rp, area_label, reason)


# ---- Live ------------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveView:
    settings: Settings
    disruptions: list[Disruption]
    hotspots: list[dict[str, Any]]
    checkins: list[Checkin]
    ripple: Ripple | None = None
    checked_at: datetime | None = None  # alerter's last successful tick (heartbeat)
    stale: bool = False
    recent: list[dict[str, Any]] = field(default_factory=list)  # resolved in the last day, newest first


def read_heartbeat(path: Path) -> datetime | None:
    try:
        tick = json.loads(Path(path).read_text()).get("tick_ok")
        return datetime.fromisoformat(tick) if tick else None
    except (OSError, ValueError):
        return None


def live_view(store: Store, network: Network, params: Params, baseline: Baseline, now: datetime,
              heartbeat: Path | None = None) -> LiveView:
    settings = Settings.from_dict(store.get_state("settings"))
    tracker = IncidentTracker.from_dict(params, store.get_state("tracker") or {})
    all_d = tracker.disruptions()
    disruptions = [d for d in all_d if d.resolved_at is None]
    recent = sorted(({"line": line_label(d.line_name), "severity": SEVERITY_WORDS[d.severity_class],
                      "ended": d.resolved_at} for d in all_d if d.resolved_at is not None),
                    key=lambda r: r["ended"], reverse=True)
    checkins = store.checkins_since(now - timedelta(minutes=30))
    checked_at = read_heartbeat(heartbeat) if heartbeat else None
    stale = checked_at is None or now - checked_at > STALE_AFTER
    rows: list[dict[str, Any]] = []
    rp = None
    sectional = [d for d in disruptions if not d.line_wide]
    if settings.has_area and sectional:
        area = ServiceArea(settings.area_lat, settings.area_lon, settings.radius_km)
        rp = ripple(network, params, baseline, sectional, now, "Dry", area, settings.idle_drivers, steps=(params.ramp_min,))
        rows = [r for r in rp.frames]
    return LiveView(settings, disruptions, rows, checkins, rp, checked_at, stale, recent)


# ---- Evidence --------------------------------------------------------------------------


def qualifying_incidents(status_dir: Path, network: Network, params: Params) -> int:
    """Distinct unplanned incidents in the logs strong enough to alert on (≥ the threshold at
    the section itself). The backtest (T11) needs about 20."""
    keys: set[str] = set()
    for _, raw in load_snapshots(status_dir):
        try:
            incidents = parse_status(json.loads(raw), network).incidents
        except ValueError:
            continue
        for i in incidents:
            base = params.severity(i.severity_class) * params.unplanned_multiplier * i.severity_scale
            if i.is_unplanned and not i.line_wide and base >= params.alert_threshold_uplift:
                keys.add(i.key)
    return len(keys)


def params_rows() -> list[dict[str, Any]]:
    """params.yaml with DESIGN.md's badge vocabulary: assumption → ESTIMATE."""
    kind = {"assumption": "ESTIMATE", "design choice": "DESIGN CHOICE"}
    return [{"parameter": name, "value": e["value"], "unit": e["unit"], "kind": kind.get(e["source"], "EVIDENCE"),
             "note": e.get("note", "")} for name, e in load_raw().items()]


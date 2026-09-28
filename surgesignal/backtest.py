"""Does SurgeSignal pick better stations than common sense? (T11, the backtest.)

London has no public ride data, so the proxy is Santander bikes: when the Tube fails, some
riders take a bike instead, and the docks near where they are stranded empty out.

    every alert-worthy disruption (the real alerter replayed over the logs, whole network)
        │
        ├─ engine:  the top stations the alert named
        ├─ closed:  every station of the disrupted section in the area      (naive rule 1)
        └─ busiest: the area's 3 busiest stations at that hour, disruption or not   (naive rule 2)
        │
        ▼ for each station: bike activity at docks within 400 m over the 30 min after the alert
          (bikes docked + undocked) minus the same docks at the same time of day (±60 min) on other days
        ▼
    excess bike moves per station, averaged per rule per disruption; reported whichever way it
    comes out (design OV#5).
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from surgesignal.alerts.format import LONDON, london_hhmm
from surgesignal.engine.geo import haversine_km, in_service_area
from surgesignal.engine.types import ServiceArea
from surgesignal.replay import Event
from surgesignal.tfl.network import Network

WINDOW = timedelta(minutes=30)
START_TOL = timedelta(minutes=10)  # first bike snapshot at most this long after the alert
END_TOL = timedelta(minutes=8)  # second snapshot this close to start + WINDOW
DOCK_RADIUS_KM = 0.4
TOD_TOL_MIN = 60
MIN_NORMALS = 2
RULES = ("engine", "closed", "busiest")


def docks_near(lat: float, lon: float, docks: Mapping[str, tuple[float, float]], km: float = DOCK_RADIUS_KM) -> list[str]:
    return sorted(d for d, (dlat, dlon) in docks.items() if haversine_km(lat, lon, dlat, dlon) <= km)


def _nearest(index: pd.DatetimeIndex, t: datetime, tol: timedelta, after_only: bool = False) -> datetime | None:
    if len(index) == 0:
        return None
    i = index.searchsorted(t)
    cands = [index[j] for j in (i - 1, i) if 0 <= j < len(index)]
    if after_only:
        cands = [c for c in cands if c >= t]
    best = min(cands, key=lambda c: abs(c - t), default=None)
    return best.to_pydatetime() if best is not None and abs(best - t) <= tol else None


def taken(bikes: pd.DataFrame, dock_ids: Sequence[str], t: datetime) -> float | None:
    """Bike activity at these docks over WINDOW from t: every bike docked or undocked between
    consecutive snapshots, summed. Activity, not net change: stranded riders take bikes near
    where they are stuck and dock them near where they go, so a disruption raises use in both
    directions, and a net count can cancel out (tried first, Sep 28: the 08:49 Central line
    closure showed bikes arriving in the City, which a net "taken" scores as negative)."""
    if not dock_ids:
        return None
    a = _nearest(bikes.index, t, START_TOL, after_only=True)
    b = _nearest(bikes.index, a + WINDOW, END_TOL) if a else None
    if a is None or b is None or b <= a:
        return None
    window = bikes.loc[a:b, list(dock_ids)]
    steps = window.index.to_series().diff().dropna()
    if len(window) < 3 or (steps > timedelta(minutes=12)).any():  # a hole in the window: can't count activity
        return None
    moves = window.diff().abs().iloc[1:]
    return float(moves.sum(min_count=1).sum()) if moves.notna().any().any() else None


def _minutes_of_day(t: datetime) -> int:
    lt = t.astimezone(LONDON)
    return lt.hour * 60 + lt.minute


def normal(bikes: pd.DataFrame, dock_ids: Sequence[str], t: datetime, exclude: Iterable[datetime] = ()) -> float | None:
    """Mean bikes taken from these docks at the same time of day on other days, away from any disruption."""
    day, tod = t.astimezone(LONDON).date(), _minutes_of_day(t)
    busy = [e for e in exclude]
    samples = []
    for start in bikes.index:
        s = start.to_pydatetime()
        if s.astimezone(LONDON).date() == day:
            continue
        diff = abs(_minutes_of_day(s) - tod)
        if min(diff, 1440 - diff) > TOD_TOL_MIN or any(abs(s - e) < timedelta(hours=2) for e in busy):
            continue
        v = taken(bikes, dock_ids, s)
        if v is not None:
            samples.append(v)
    return statistics.fmean(samples) if len(samples) >= MIN_NORMALS else None


def excess(bikes: pd.DataFrame, dock_ids: Sequence[str], t: datetime, exclude: Iterable[datetime] = ()) -> float | None:
    now = taken(bikes, dock_ids, t)
    if now is None:
        return None
    usual = normal(bikes, dock_ids, t, exclude)
    return None if usual is None else now - usual


def busiest_in(network: Network, area: ServiceArea, busyness: Mapping[str, float], n: int = 3) -> list[str]:
    """Where a dispatcher parks cars on a normal day: the area's busiest stations at that hour."""
    near = [s for s, st in network.stations.items() if in_service_area(st, area)]
    return sorted(near, key=lambda s: (-busyness.get(s, 0.0), s))[:n]


@dataclass(frozen=True)
class Scored:
    at: str  # ISO
    label: str  # "District line, Wed 23 Sep 19:46"
    engine: float | None
    closed: float | None
    busiest: float | None


def score_events(events: Sequence[Event], network: Network, area: ServiceArea, bikes: pd.DataFrame,
                 docks: Mapping[str, tuple[float, float]], busyness_at) -> list[Scored]:
    """busyness_at(t) -> {station id: relative busyness}: the baseline, for naive rule 2."""
    times = [e.at for e in events]
    near_cache: dict[str, list[str]] = {}

    def station_excess(sid: str, t: datetime) -> float | None:
        if sid not in near_cache:
            st = network.stations[sid]
            near_cache[sid] = docks_near(st.lat, st.lon, docks)
        return excess(bikes, near_cache[sid], t, exclude=times)

    def rule(ids: Iterable[str], t: datetime) -> float | None:
        vals = [v for v in (station_excess(s, t) for s in dict.fromkeys(ids) if s in network.stations) if v is not None]
        return statistics.fmean(vals) if vals else None

    out = []
    for e in events:
        section = [s for ids in e.sections.values() for s in ids
                   if s in network.stations and in_service_area(network.stations[s], area)]
        engine = [s for ids in e.top_ids.values() for s in ids]
        if not section or not engine:
            continue
        line = e.text.split(" (")[0].lstrip("⚠️ ").strip()
        label = f"{line}, {e.at.astimezone(LONDON):%a %d %b} {london_hhmm(e.at)}"
        out.append(Scored(e.at.isoformat(), label, rule(engine, e.at), rule(section, e.at),
                          rule(busiest_in(network, area, busyness_at(e.at)), e.at)))
    return out


def _interval(xs: Sequence[float], seed: int = 7, n: int = 2000) -> tuple[float, float]:
    """90% bootstrap interval of the mean (fixed seed: the published numbers don't wobble)."""
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(n))
    return means[int(0.05 * n)], means[int(0.95 * n) - 1]


@dataclass(frozen=True)
class RuleSummary:
    rule: str
    n: int
    mean: float
    low: float
    high: float


def summarise(scored: Sequence[Scored]) -> dict[str, Any]:
    """Compare the rules on the disruptions where all three could be measured."""
    both = [s for s in scored if None not in (s.engine, s.closed, s.busiest)]
    rules = []
    for r in RULES:
        xs = [getattr(s, r) for s in both]
        if xs:
            lo, hi = _interval(xs)
            rules.append(RuleSummary(r, len(xs), statistics.fmean(xs), lo, hi))
    beats = {other: sum(1 for s in both if s.engine > getattr(s, other)) for other in ("closed", "busiest")}
    return {"measured": len(both), "events": len(scored), "rules": [asdict(r) for r in rules], "engine_beats": beats,
            "per_event": [asdict(s) for s in both]}

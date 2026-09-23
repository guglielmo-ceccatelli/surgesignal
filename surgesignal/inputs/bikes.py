"""Bike-dock occupancy summarised per station: the backtest's proxy for people leaving a station
area another way (T11). Small enough to keep in git, unlike the ~2 MB raw /BikePoint response.

    /BikePoint (≈800 docks) ─► installed, unlocked docks within RADIUS_KM of each station
        ─► per station: bikes, empty docks, total docks, dock count ─► one CSV row

A disruption that pushes people onto bikes shows up as bikes falling (and empty docks rising)
near the affected stations, compared with the same time on normal days.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from surgesignal.engine.geo import haversine_km
from surgesignal.engine.types import Station

RADIUS_KM = 0.5
FIELDS = ("station_id", "bikes", "empty_docks", "docks", "points")


@dataclass(frozen=True)
class StationBikes:
    station_id: str
    bikes: int
    empty_docks: int
    docks: int
    points: int  # how many bike docks were summed


def _props(place: dict[str, Any]) -> dict[str, str]:
    return {p.get("key"): p.get("value") for p in place.get("additionalProperties", [])}


def summarise(bikepoints: list[dict[str, Any]], stations: Mapping[str, Station], radius_km: float = RADIUS_KM) -> list[StationBikes]:
    """Stations with at least one working dock within radius_km, in station id order."""
    docks = []
    for place in bikepoints:
        props = _props(place)
        if props.get("Installed") != "true" or props.get("Locked") == "true":
            continue
        try:
            docks.append((float(place["lat"]), float(place["lon"]), int(props["NbBikes"]),
                          int(props["NbEmptyDocks"]), int(props["NbDocks"])))
        except (KeyError, TypeError, ValueError):
            continue  # a malformed dock is skipped, never fatal
    out = []
    for sid in sorted(stations):
        s = stations[sid]
        near = [d for d in docks if haversine_km(s.lat, s.lon, d[0], d[1]) <= radius_km]
        if near:
            out.append(StationBikes(sid, sum(d[2] for d in near), sum(d[3] for d in near), sum(d[4] for d in near), len(near)))
    return out


def to_csv(rows: list[StationBikes]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(FIELDS)
    for r in rows:
        w.writerow((r.station_id, r.bikes, r.empty_docks, r.docks, r.points))
    return buf.getvalue()


def from_csv(text: str) -> list[StationBikes]:
    return [StationBikes(r["station_id"], int(r["bikes"]), int(r["empty_docks"]), int(r["docks"]), int(r["points"]))
            for r in csv.DictReader(io.StringIO(text))]

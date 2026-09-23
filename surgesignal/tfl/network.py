"""The rail network the parser expands disruption sections over: stations + ordered routes per line.

Built once from TfL `/Line/{id}/Route/Sequence/outbound` (scripts/build_network.py) and committed
as surgesignal/data/network.json, so the alerter never needs these calls at runtime.

    Route/Sequence JSON ──► stations {stationId: name, lat, lon}   (shared across lines)
                        └─► lines {line: routes [[stationId, ...] in running order]}
                             e.g. northern: "Morden <-> High Barnet via Bank" = [MDN, SWN, ..., HBT]

Sections are expanded along these routes (parser.expand_section), so "between Stockwell and
Morden" becomes every station in between, branch-aware.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from surgesignal.engine.geo import haversine_km
from surgesignal.engine.types import Station
from surgesignal.tfl.names import display_name, lookup_keys

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "network.json"
SAME_PLACE_KM = 1.0


@dataclass(frozen=True)
class Route:
    name: str
    station_ids: tuple[str, ...]


@dataclass(frozen=True)
class Line:
    id: str
    name: str
    routes: tuple[Route, ...]

    @property
    def station_ids(self) -> frozenset[str]:
        return frozenset(s for r in self.routes for s in r.station_ids)


@dataclass(frozen=True)
class Network:
    stations: dict[str, Station]
    lines: dict[str, Line]
    naptan_to_station: dict[str, str]  # stop-point ids (as in affectedStops) -> stationId

    def name_index(self, line_id: str) -> dict[str, tuple[str, ...]]:
        """Normalised name variant -> stationIds on one line.

        One name can mean several ids at the same place (Paddington's Circle and H&C platforms,
        Elizabeth line high- and low-level Liverpool Street): kept together when all lie within
        SAME_PLACE_KM, so a section can start from whichever the route uses. Names shared by
        stations further apart are genuinely ambiguous and dropped rather than guessed.
        """
        groups: dict[str, set[str]] = {}
        for sid in self.lines[line_id].station_ids:
            for key in lookup_keys(self.stations[sid].name):
                groups.setdefault(key, set()).add(sid)
        return {k: tuple(sorted(ids)) for k, ids in groups.items() if self._same_place(ids)}

    def _same_place(self, ids: set[str]) -> bool:
        pts = [self.stations[i] for i in ids]
        return all(
            haversine_km(a.lat, a.lon, b.lat, b.lon) <= SAME_PLACE_KM for a in pts for b in pts
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "stations": {sid: {"name": s.name, "lat": s.lat, "lon": s.lon} for sid, s in sorted(self.stations.items())},
            "lines": {
                lid: {"name": line.name, "routes": [{"name": r.name, "stations": list(r.station_ids)} for r in line.routes]}
                for lid, line in sorted(self.lines.items())
            },
            "naptan_to_station": dict(sorted(self.naptan_to_station.items())),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Network:
        stations = {sid: Station(sid, v["name"], v["lat"], v["lon"]) for sid, v in data["stations"].items()}
        lines = {
            lid: Line(lid, v["name"], tuple(Route(r["name"], tuple(r["stations"])) for r in v["routes"]))
            for lid, v in data["lines"].items()
        }
        return cls(stations, lines, dict(data["naptan_to_station"]))


def load_network(path: Path = DEFAULT_PATH) -> Network:
    return Network.from_json(json.loads(Path(path).read_text()))


def build_network(route_sequences: Iterable[dict[str, Any]]) -> Network:
    """Build from raw TfL Route/Sequence responses (one per line)."""
    stations: dict[str, Station] = {}
    lines: dict[str, Line] = {}
    naptan: dict[str, str] = {}
    for seq in route_sequences:
        for group in seq["stopPointSequences"]:
            for sp in group["stopPoint"]:
                sid = sp.get("stationId") or sp.get("topMostParentId") or sp["id"]
                naptan[sp["id"]] = sid
                stations.setdefault(sid, Station(sid, display_name(sp["name"]), sp["lat"], sp["lon"]))
        routes = []
        for r in seq["orderedLineRoutes"]:
            ids = tuple(naptan[i] for i in r["naptanIds"])
            name = " ".join(r["name"].replace("&harr;", "<->").split())
            routes.append(Route(name, ids))
        lines[seq["lineId"]] = Line(seq["lineId"], seq["lineName"], tuple(routes))
    return Network(stations, lines, naptan)

"""Distances and the dispatcher's service area. Straight-line only (network distance is a TODO)."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from surgesignal.engine.types import ServiceArea, Station

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def distance_to_section(station: Station, affected_ids: Iterable[str], stations: Mapping[str, Station]) -> float:
    """Minimum distance from station to any affected station; 0 if it is itself affected.

    Raises KeyError for an affected id the station list doesn't know: the parser only
    emits ids from the same list, so an unknown id is a bug to surface, not skip.
    """
    ids = tuple(affected_ids)
    if station.id in ids:
        return 0.0
    return min(haversine_km(station.lat, station.lon, stations[i].lat, stations[i].lon) for i in ids)


def in_service_area(station: Station, area: ServiceArea) -> bool:
    return haversine_km(station.lat, station.lon, area.lat, area.lon) <= area.radius_km

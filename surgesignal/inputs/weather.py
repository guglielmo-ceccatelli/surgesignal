"""Current weather at the dispatcher's area from Open-Meteo (free, no key).

    fetch(lat, lon) ─► {"current": {"precipitation": mm in the last hour, "temperature_2m": °C}}
                    ─► Weather(precip_mm_h, temp_c), cached for CACHE_FOR

When Open-Meteo is unreachable (network filter, outage) `current()` returns None: the alerter then
scores as dry weather and tells the builder once a day. Alerts never wait for the weather.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from workers.logger import http_fetch

URL = "https://api.open-meteo.com/v1/forecast"
CACHE_FOR = timedelta(minutes=10)


@dataclass(frozen=True)
class Weather:
    precip_mm_h: float
    temp_c: float


def open_meteo_url(lat: float, lon: float) -> str:
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "current": "precipitation,temperature_2m", "timezone": "UTC"}
    return URL + "?" + urllib.parse.urlencode(q)


def parse(payload: bytes) -> Weather:
    cur = json.loads(payload)["current"]
    return Weather(precip_mm_h=float(cur["precipitation"]), temp_c=float(cur["temperature_2m"]))


class WeatherSource:
    def __init__(self, fetch: Callable[[str], bytes] = http_fetch) -> None:
        self.fetch = fetch
        self._cache: tuple[datetime, tuple[float, float], Weather] | None = None
        self.last_error: str | None = None

    def current(self, lat: float, lon: float, now: datetime) -> Weather | None:
        key = (round(lat, 3), round(lon, 3))
        if self._cache and self._cache[1] == key and now - self._cache[0] < CACHE_FOR:
            return self._cache[2]
        try:
            w = parse(self.fetch(open_meteo_url(lat, lon)))
        except Exception as exc:  # blocked, down, or malformed: score as dry, report upstream
            self.last_error = repr(exc)
            return None
        self.last_error = None
        self._cache = (now, key, w)
        return w

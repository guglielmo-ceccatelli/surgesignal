"""Dispatcher settings (set from Telegram) and parsing of the setting commands.

    /idle 6                     idle cars usually free
    /area Clapham Common 5      centre on a station, radius in km (default 5)
    /area SW4 7AA 3             or a UK postcode (looked up on postcodes.io)
    /quiet 23:00-06:00 | off    no alerts in these London hours
    /lookahead 18:00 | off      daily look-ahead at this London time (planned closures, strikes, events)
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import time
from typing import Any

from surgesignal.tfl.names import lookup_keys, normalise
from surgesignal.tfl.network import Network

POSTCODE = re.compile(r"^([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})$", re.I)
MAX_RADIUS_KM = 30
MAX_IDLE = 500


@dataclass(frozen=True)
class Settings:
    idle_drivers: int | None = None
    area_lat: float | None = None
    area_lon: float | None = None
    area_label: str | None = None
    radius_km: float = 5.0
    quiet_start: str | None = None  # "HH:MM", London time
    quiet_end: str | None = None
    lookahead_at: str | None = "18:00"  # "HH:MM" London, None = off

    @property
    def has_area(self) -> bool:
        return self.area_lat is not None and self.area_lon is not None

    @property
    def quiet(self) -> tuple[time, time] | None:
        if self.quiet_start and self.quiet_end:
            return time.fromisoformat(self.quiet_start), time.fromisoformat(self.quiet_end)
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Settings:
        return cls(**d) if d else cls()

    def describe(self) -> str:
        area = f"{self.area_label}, {self.radius_km:g} km" if self.has_area else "not set (use /area)"
        idle = str(self.idle_drivers) if self.idle_drivers is not None else "not set (use /idle)"
        quiet = f"{self.quiet_start}–{self.quiet_end}" if self.quiet else "off"
        ahead = f"daily at {self.lookahead_at}" if self.lookahead_at else "off"
        return f"Area: {area}\nIdle cars: {idle}\nQuiet hours: {quiet}\nLook-ahead: {ahead}"


class SettingsError(ValueError):
    pass


def set_idle(s: Settings, arg: str) -> Settings:
    try:
        n = int(arg.strip())
    except ValueError:
        raise SettingsError("Usage: /idle 6  (how many cars are usually free)") from None
    if not 0 <= n <= MAX_IDLE:
        raise SettingsError(f"Idle cars must be between 0 and {MAX_IDLE}.")
    return replace(s, idle_drivers=n)


PostcodeLookup = Callable[[str], tuple[float, float]]


def postcodes_io(pc: str) -> tuple[float, float]:
    url = "https://api.postcodes.io/postcodes/" + urllib.parse.quote(pc)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            r = json.loads(resp.read())["result"]
    except Exception as exc:  # 404 for unknown postcodes, network errors
        raise SettingsError(f"Couldn't find postcode {pc.upper()}.") from exc
    return r["latitude"], r["longitude"]


def set_area(s: Settings, arg: str, network: Network, lookup: PostcodeLookup = postcodes_io) -> Settings:
    words = arg.split()
    radius = s.radius_km
    if words and re.fullmatch(r"\d+(\.\d+)?", words[-1]) and len(words) > 1:
        radius = float(words.pop())
    place = " ".join(words)
    if not place:
        raise SettingsError("Usage: /area Clapham Common 5  or  /area SW4 7AA 3")
    if not 0 < radius <= MAX_RADIUS_KM:
        raise SettingsError(f"Radius must be between 0 and {MAX_RADIUS_KM} km.")
    if POSTCODE.match(place):
        lat, lon = lookup(place)
        return replace(s, area_lat=lat, area_lon=lon, area_label=place.upper(), radius_km=radius)
    key = normalise(place)
    matches = sorted((st for st in network.stations.values() if key in lookup_keys(st.name)), key=lambda st: st.id)
    if not matches:
        raise SettingsError(f"No station called '{place}'. Try the name as on the Tube map, or a postcode.")
    st = matches[0]
    return replace(s, area_lat=st.lat, area_lon=st.lon, area_label=st.name, radius_km=radius)


def set_quiet(s: Settings, arg: str) -> Settings:
    arg = arg.strip().lower()
    if arg == "off":
        return replace(s, quiet_start=None, quiet_end=None)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", arg)
    if not m:
        raise SettingsError("Usage: /quiet 23:00-06:00  or  /quiet off")
    h1, m1, h2, m2 = map(int, m.groups())
    if not (h1 < 24 and h2 < 24 and m1 < 60 and m2 < 60) or (h1, m1) == (h2, m2):
        raise SettingsError("Use two different times like 23:00-06:00.")
    return replace(s, quiet_start=f"{h1:02d}:{m1:02d}", quiet_end=f"{h2:02d}:{m2:02d}")


def set_lookahead(s: Settings, arg: str) -> Settings:
    arg = arg.strip().lower()
    if arg == "off":
        return replace(s, lookahead_at=None)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", arg)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise SettingsError("Usage: /lookahead 18:00  or  /lookahead off")
    return replace(s, lookahead_at=f"{int(m.group(1)):02d}:{m.group(2)}")

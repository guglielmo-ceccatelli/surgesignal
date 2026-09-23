"""Plain data types passed into and out of the engine. No I/O, no clock."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

SEVERITY_CLASSES = ("suspended", "part_suspended", "severe", "minor")


@dataclass(frozen=True)
class Station:
    id: str  # TfL stationId, e.g. "940GZZLUSKW"
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Disruption:
    """One incident, as produced by the parser (T4).

    line_wide: TfL named no section, so the engine must NOT rank stations for it (OV#2).
    severity_scale: 0.5 when the parser fell back to the whole line because no station
        names matched (area_uncertain), else 1.0 (eng review 5A).
    resolved_at: set once TfL reports good service again; demand then decays.
    """

    key: str
    line: str
    line_name: str
    severity_class: str
    is_unplanned: bool
    affected_station_ids: tuple[str, ...]
    t0: datetime
    line_wide: bool = False
    area_uncertain: bool = False
    severity_scale: float = 1.0
    expected_duration_min: float | None = None
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.severity_class not in SEVERITY_CLASSES:
            raise ValueError(f"unknown severity_class {self.severity_class!r}")
        if self.t0.tzinfo is None:
            raise ValueError("t0 must be timezone-aware (UTC)")
        if not self.line_wide and not self.affected_station_ids:
            raise ValueError(f"disruption {self.key} has no stations and is not line_wide")


@dataclass(frozen=True)
class Event:
    name: str
    lat: float
    lon: float
    capacity: int
    ends_at: datetime


@dataclass(frozen=True)
class Conditions:
    precip_mm_h: float = 0.0
    temp_c: float = 15.0
    events: tuple[Event, ...] = ()
    time_label: str | None = None  # e.g. "Friday peak", from the baseline profile


@dataclass(frozen=True)
class ServiceArea:
    lat: float
    lon: float
    radius_km: float


@dataclass(frozen=True)
class Range:
    low: float
    mid: float
    high: float


@dataclass(frozen=True)
class Hotspot:
    station: Station
    uplift: Range  # disruption-only uplift, fraction of baseline
    pct: Range  # what the dispatcher sees: (1 + uplift) * mult - 1
    mult: float  # weather x event multiplier
    extra: float  # baseline * pct.mid, used for ranking and car allocation
    explanation: str
    disruption_keys: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class EngineResult:
    hotspots: tuple[Hotspot, ...]  # in service area, ranked by extra, pct.mid > 0
    line_wide: tuple[Disruption, ...]  # alert as "line-wide, no station suggestion"
    area_uncertain: tuple[Disruption, ...]  # flag on the alert + builder ping

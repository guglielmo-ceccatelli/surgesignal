"""How often would a dispatcher actually get an alert? (T8, the week-one count.)

The logs only cover the hours the logging Mac was awake, so a raw count says little on its own.
This turns it into a rate:

    bike snapshots every ~5 min while awake ─► covered segments (a gap > 20 min ends one)
    status snapshots (change-only)          ─► replayed through the real alerter, segment by
                                               segment, with one shared state (an incident that
                                               spans a gap alerts once, as it would live)
    "new" alerts during open hours (06:00–24:00 London) ÷ open hours covered × 126 h
                                            ─► alerts per week for that area

Open hours only: the Tube is mostly shut overnight and the Mac is mostly asleep then, so
counting night hours would dilute the rate with time nobody watched.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from surgesignal.alerts.format import LONDON
from surgesignal.alerts.settings import Settings
from surgesignal.engine.params import Params
from surgesignal.replay import Event, replay
from surgesignal.tfl.network import Network

MAX_GAP = timedelta(minutes=20)
OPEN_FROM, OPEN_TO = 6, 24  # London hours
OPEN_HOURS_PER_WEEK = (OPEN_TO - OPEN_FROM) * 7

Snapshot = tuple[datetime, bytes]
Segment = tuple[datetime, datetime]


def covered_segments(times: Sequence[datetime], max_gap: timedelta = MAX_GAP) -> list[Segment]:
    """Runs of timestamps with no gap longer than `max_gap`. A lone timestamp covers nothing."""
    out: list[Segment] = []
    ts = sorted(times)
    start = prev = ts[0] if ts else None
    for t in ts[1:]:
        if t - prev > max_gap:
            if prev > start:
                out.append((start, prev))
            start = t
        prev = t
    if ts and prev > start:
        out.append((start, prev))
    return out


def is_open(t: datetime) -> bool:
    return OPEN_FROM <= t.astimezone(LONDON).hour < OPEN_TO


def open_hours(segments: Sequence[Segment], step: timedelta = timedelta(minutes=5)) -> float:
    """Hours of the segments that fall in open hours (sampled every `step`)."""
    total = timedelta()
    for start, end in segments:
        t = start
        while t < end:
            dt = min(step, end - t)
            if is_open(t):
                total += dt
            t += dt
    return total.total_seconds() / 3600


def segment_snapshots(snapshots: Sequence[Snapshot], seg: Segment) -> list[Snapshot]:
    """Status snapshots for one segment. Status is saved only when it changes, so the last one
    before the segment still holds at its start: carry it in, re-timed to the start."""
    start, end = seg
    before = [s for s in snapshots if s[0] <= start]
    inside = [s for s in snapshots if start < s[0] <= end]
    carried = [(start, before[-1][1])] if before else []
    return carried + inside if carried or inside else []


def alerts_in(snapshots: Sequence[Snapshot], segments: Sequence[Segment], network: Network, params: Params,
              settings: Settings, step: timedelta = timedelta(minutes=1)) -> list[Event]:
    """Replay each covered segment in order, sharing the alerter's state. Returns the "new" alerts."""
    out: list[Event] = []
    with tempfile.TemporaryDirectory() as tmp:
        for seg in segments:
            snaps = segment_snapshots(snapshots, seg)
            if not snaps:
                continue
            # the replay runs from the first snapshot to the last + tail: pin the end to the segment's
            snaps = snaps + [(seg[1], snaps[-1][1])] if snaps[-1][0] < seg[1] else snaps
            result = replay(snaps, network, params, settings, step=step, tail=timedelta(0), state_dir=Path(tmp))
            out += result.of("new")
    return out


@dataclass(frozen=True)
class AreaRate:
    label: str
    radius_km: float
    alerts: int  # actionable "new" alerts (⚠️: stations and cars) in open hours
    per_week: float
    info: int = 0  # line-wide notices (ℹ️: TfL named no section, so no cars to move)


@dataclass(frozen=True)
class WeekCount:
    as_of: str  # London date
    first: str
    last: str
    covered_hours: float
    open_hours: float
    london_alerts: int  # a dispatcher covering all of London: the qualifying-disruption count
    areas: list[AreaRate]

    @property
    def typical_per_week(self) -> float:
        """Median over the sample areas: what one firm in one part of London would see."""
        rates = sorted(a.per_week for a in self.areas)
        if not rates:
            return 0.0
        mid = len(rates) // 2
        return rates[mid] if len(rates) % 2 else (rates[mid - 1] + rates[mid]) / 2

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["typical_per_week"] = round(self.typical_per_week, 1)
        return d


def is_actionable(e: Event) -> bool:
    return e.text.startswith("⚠️")


def rate(alerts: Sequence[Event], hours_open: float) -> tuple[int, float, int]:
    """(actionable alerts in open hours, per week, line-wide notices in open hours)."""
    seen = [e for e in alerts if is_open(e.at)]
    n = sum(1 for e in seen if is_actionable(e))
    return n, (n / hours_open * OPEN_HOURS_PER_WEEK if hours_open else 0.0), len(seen) - n


def load_evidence(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None

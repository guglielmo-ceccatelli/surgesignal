"""Big events that end near a station: hand-entered fixtures joined to a venue list.

    surgesignal/data/venues.csv   venue, lat, lon, capacity, note      (committed; rarely changes)
    events.csv  (repo root)       venue, name, ends_at                 (you edit; London local time)

        Wembley Stadium,England v Brazil,2026-10-14 21:45

The alerter re-reads events.csv every tick, so edits apply without a restart. Bad rows are skipped
and reported (once a day) instead of stopping alerts.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from surgesignal.engine.types import Event
from surgesignal.inputs.baseline import LONDON

VENUES_PATH = Path(__file__).resolve().parent.parent / "data" / "venues.csv"
EVENTS_PATH = Path(__file__).resolve().parent.parent.parent / "events.csv"


@dataclass(frozen=True)
class Venue:
    name: str
    lat: float
    lon: float
    capacity: int


def load_venues(path: Path = VENUES_PATH) -> dict[str, Venue]:
    with open(path, newline="") as f:
        return {r["venue"].strip(): Venue(r["venue"].strip(), float(r["lat"]), float(r["lon"]), int(r["capacity"]))
                for r in csv.DictReader(f)}


def load_events(venues: dict[str, Venue], path: Path = EVENTS_PATH) -> tuple[list[Event], list[str]]:
    """Events from the fixtures file, plus a problem message per unusable row. Missing file → none."""
    if not Path(path).exists():
        return [], []
    events, problems = [], []
    with open(path, newline="") as f:
        lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    for n, row in enumerate(csv.DictReader(lines), start=2):
        venue = venues.get((row.get("venue") or "").strip())
        if venue is None:
            problems.append(f"events.csv row {n}: unknown venue '{row.get('venue')}' (see venues.csv)")
            continue
        try:
            ends = datetime.strptime((row.get("ends_at") or "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=LONDON)
        except ValueError:
            problems.append(f"events.csv row {n}: ends_at must look like 2026-10-14 21:45 (London time)")
            continue
        events.append(Event((row.get("name") or venue.name).strip(), venue.lat, venue.lon, venue.capacity, ends))
    return events, problems

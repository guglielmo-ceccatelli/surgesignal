"""How busy each station normally is at a given time, from surgesignal/data/baseline.json.

    busyness(station, t) = size(station, day type) × shape(station, day, 15-min band)
                           (≈ 1.0 for a median station at an average moment of its day)

Built by scripts/build_baseline.py from TfL's annual station counts and crowding profiles.
Times are converted to London time here: the profiles are in local clock time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "baseline.json"
LONDON = ZoneInfo("Europe/London")
DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
DAY_NAMES = dict(zip(DAYS, ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")))


def day_type(day: str) -> str:
    """Day → the AC2023 count column: Monday, midweek (Tue–Thu), Friday, Saturday, Sunday."""
    return "MIDWEEK" if day in ("TUE", "WED", "THU") else day


@dataclass(frozen=True)
class Baseline:
    data: dict[str, Any]

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> Baseline:
        return cls(json.loads(Path(path).read_text()))

    @staticmethod
    def _slot(t: datetime) -> tuple[str, int]:
        if t.tzinfo is None:
            raise ValueError("t must be timezone-aware")
        local = t.astimezone(LONDON)
        return DAYS[local.weekday()], (local.hour * 60 + local.minute) // 15

    def at(self, t: datetime) -> dict[str, float]:
        """Relative busyness of every station at t."""
        day, band = self._slot(t)
        size_key = day_type(day)
        network = self.data["network_shape"][day][band]
        out = {}
        for sid, st in self.data["stations"].items():
            shape = (st["shape"].get(day) or [None] * 96)[band]
            out[sid] = st["size"][size_key] * (shape if shape is not None else network)
        return out

    def time_label(self, t: datetime) -> str | None:
        """"Friday evening peak" when t falls in London's typical weekday peak windows, else None."""
        day, band = self._slot(t)
        peaks = self.data["peaks"].get(day)
        if not peaks:
            return None
        for which, name in (("am", "morning"), ("pm", "evening")):
            start, end = peaks[which]
            if start <= band < end:
                return f"{DAY_NAMES[day]} {name} peak"
        return None

    def sources(self, station_id: str) -> dict[str, Any]:
        """Where a station's numbers came from (for the console's "how this works" panel)."""
        st = self.data["stations"][station_id]
        return {"size": st["size_source"], "shape": {d: ("station" if s else "network") for d, s in st["shape"].items()}}

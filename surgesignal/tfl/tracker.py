"""Give parsed incidents a memory: start time, resolution time, and flap-merging.

The parser sees one poll at a time; the engine needs t0 and resolved_at. The tracker keeps:

    key ──► first seen (or TfL's own start time) ──► still reported? ── yes ─► active
                                                                      └─ no ──► resolved_at = now
    resolved and reappears within incident_merge_window_min ─► same incident, original t0 (flap)
    resolved longer than resolved_retention_min ─► forgotten

State round-trips through to_dict()/from_dict() so the alerter can persist it (eng review 3A)
and a restart doesn't reset t0 or re-announce incidents.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from surgesignal.engine.params import Params
from surgesignal.engine.types import Disruption
from surgesignal.tfl.parser import Incident


@dataclass(frozen=True)
class Tracked:
    incident: Incident
    t0: datetime
    resolved_at: datetime | None = None


class IncidentTracker:
    def __init__(self, params: Params, state: dict[str, Tracked] | None = None) -> None:
        self.p = params
        self.state: dict[str, Tracked] = dict(state or {})

    def update(self, incidents: list[Incident], now: datetime) -> list[Disruption]:
        """Feed one poll's incidents; return every disruption the engine should score now."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware (UTC)")
        seen = {inc.key for inc in incidents}

        for inc in incidents:
            prev = self.state.get(inc.key)
            if prev is None or (prev.resolved_at is not None and now - prev.resolved_at > self._merge):
                t0 = min(inc.started_at, now) if inc.started_at else now
                self.state[inc.key] = Tracked(inc, t0)
            else:
                # still active, or a flap back within the merge window: keep t0, refresh details
                self.state[inc.key] = Tracked(inc, prev.t0, None)

        for key, tr in list(self.state.items()):
            if key not in seen and tr.resolved_at is None:
                self.state[key] = replace(tr, resolved_at=now)
            elif tr.resolved_at is not None and now - tr.resolved_at > self._retention:
                del self.state[key]

        return [self._to_disruption(tr) for tr in self.state.values()]

    @property
    def _merge(self) -> timedelta:
        return timedelta(minutes=self.p.incident_merge_window_min)

    @property
    def _retention(self) -> timedelta:
        return timedelta(minutes=self.p.resolved_retention_min)

    @staticmethod
    def _to_disruption(tr: Tracked) -> Disruption:
        inc = tr.incident
        return Disruption(
            key=inc.key,
            line=inc.line,
            line_name=inc.line_name,
            severity_class=inc.severity_class,
            is_unplanned=inc.is_unplanned,
            affected_station_ids=inc.station_ids,
            t0=tr.t0,
            line_wide=inc.line_wide,
            area_uncertain=inc.area_uncertain,
            severity_scale=inc.severity_scale,
            resolved_at=tr.resolved_at,
        )

    def to_dict(self) -> dict[str, Any]:
        def enc(tr: Tracked) -> dict[str, Any]:
            inc = asdict(tr.incident)
            inc["station_ids"] = list(inc["station_ids"])
            inc["started_at"] = tr.incident.started_at.isoformat() if tr.incident.started_at else None
            inc["periods"] = [[a.isoformat(), b.isoformat()] for a, b in tr.incident.periods]
            return {"incident": inc, "t0": tr.t0.isoformat(),
                    "resolved_at": tr.resolved_at.isoformat() if tr.resolved_at else None}
        return {k: enc(v) for k, v in self.state.items()}

    @classmethod
    def from_dict(cls, params: Params, data: dict[str, Any]) -> IncidentTracker:
        def dec(d: dict[str, Any]) -> Tracked:
            inc = dict(d["incident"])
            inc["station_ids"] = tuple(inc["station_ids"])
            inc["started_at"] = datetime.fromisoformat(inc["started_at"]) if inc.get("started_at") else None
            inc["periods"] = tuple((datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in inc.get("periods", []))
            return Tracked(Incident(**inc), datetime.fromisoformat(d["t0"]),
                           datetime.fromisoformat(d["resolved_at"]) if d.get("resolved_at") else None)
        return cls(params, {k: dec(v) for k, v in data.items()})

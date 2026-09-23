"""Alert lifecycle: decide what to tell the dispatcher, given what they were already told.

Pure: (disruptions, engine result, previous alert records, now) -> actions. The alerter
executes the actions against Telegram and persists the records, so a restart changes nothing
(eng review 3A).

    per disruption key:

      (none) ──qualifies──► NEW ─────────────────────────────► active
                                                                │
      active ──severity class changed (immediately)──► EDIT ────┤
      active ──top stations changed, cooldown passed──► EDIT ───┤
      active ──resolved for ≥ merge window───────────► RESOLVED ┴─► resolved
      resolved ──comes back after the merge window──► NEW (new incident, new t0)

    qualifies: a top in-area station has disruption uplift ≥ alert_threshold_uplift, or, for a
    line-wide incident, the base severity alone clears the threshold and the line serves the area.
    Several NEW keys in one tick share one message (a line reported in two sections, say).
    Quiet hours hold every send; nothing is marked sent, so it goes out when they end.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Any

from surgesignal.alerts.format import LONDON, format_alert, format_line_wide, london_hhmm
from surgesignal.engine.params import Params
from surgesignal.engine.types import Disruption, EngineResult, Hotspot


@dataclass(frozen=True)
class AlertRecord:
    key: str
    group: tuple[str, ...]  # keys sharing one Telegram message
    message_id: int | None
    state: str  # "active" | "resolved"
    severity_class: str
    top_ids: tuple[str, ...]
    last_sent_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "group": list(self.group), "message_id": self.message_id, "state": self.state,
                "severity_class": self.severity_class, "top_ids": list(self.top_ids),
                "last_sent_at": self.last_sent_at.isoformat()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AlertRecord:
        return cls(d["key"], tuple(d["group"]), d["message_id"], d["state"], d["severity_class"],
                   tuple(d["top_ids"]), datetime.fromisoformat(d["last_sent_at"]))


@dataclass(frozen=True)
class SendNew:
    keys: tuple[str, ...]
    text: str
    top_ids: dict[str, tuple[str, ...]]  # per key, for the records
    severity: dict[str, str]


@dataclass(frozen=True)
class EditExisting:
    message_id: int | None
    keys: tuple[str, ...]  # the whole group whose message is re-rendered
    text: str
    top_ids: dict[str, tuple[str, ...]]
    severity: dict[str, str]


@dataclass(frozen=True)
class SendResolved:
    key: str
    reply_to: int | None
    text: str


Action = SendNew | EditExisting | SendResolved


@dataclass(frozen=True)
class Context:
    """Everything the decision needs besides disruptions and records."""

    result: EngineResult
    line_stations: Mapping[str, frozenset[str]]
    stations_in_area: frozenset[str]
    idle_drivers: int | None
    suppressed_station_ids: frozenset[str] = frozenset()  # "dead" check-ins in the last 30 min
    quiet: tuple[time, time] | None = None


def in_quiet_hours(now: datetime, quiet: tuple[time, time] | None) -> bool:
    if quiet is None:
        return False
    t = now.astimezone(LONDON).time()
    start, end = quiet
    return start <= t < end if start < end else (t >= start or t < end)  # may wrap midnight


def top_stations(d: Disruption, ctx: Context, p: Params) -> list[Hotspot]:
    hs = [h for h in ctx.result.hotspots if d.key in h.disruption_keys and h.station.id not in ctx.suppressed_station_ids]
    return hs[: p.top_n]


def qualifies(d: Disruption, ctx: Context, p: Params) -> bool:
    if d.line_wide:
        base = p.severity(d.severity_class) * (p.unplanned_multiplier if d.is_unplanned else p.planned_multiplier)
        return base >= p.alert_threshold_uplift and bool(ctx.line_stations.get(d.line, frozenset()) & ctx.stations_in_area)
    return any(h.uplift.mid >= p.alert_threshold_uplift for h in top_stations(d, ctx, p))


def render(ds: Sequence[Disruption], ctx: Context, now: datetime, p: Params) -> str:
    parts = []
    for d in ds:
        if d.line_wide:
            parts.append(format_line_wide(d))
        else:
            parts.append(format_alert(d, top_stations(d, ctx, p), ctx.idle_drivers, now, p, d.area_uncertain))
    return "\n\n".join(parts)


def decide(disruptions: Sequence[Disruption], records: Mapping[str, AlertRecord], ctx: Context,
           now: datetime, p: Params) -> list[Action]:
    if in_quiet_hours(now, ctx.quiet):
        return []
    by_key = {d.key: d for d in disruptions}
    active = [d for d in disruptions if d.resolved_at is None]
    cooldown = timedelta(minutes=p.incident_merge_window_min)
    actions: list[Action] = []

    def tops(d: Disruption) -> tuple[str, ...]:
        return tuple(h.station.id for h in top_stations(d, ctx, p))

    # NEW: qualifying incidents never alerted, or alerted and since resolved (a fresh incident)
    fresh = [d for d in active if (d.key not in records or records[d.key].state == "resolved") and qualifies(d, ctx, p)]
    if fresh:
        actions.append(SendNew(
            keys=tuple(d.key for d in fresh),
            text=render(fresh, ctx, now, p),
            top_ids={d.key: tops(d) for d in fresh},
            severity={d.key: d.severity_class for d in fresh},
        ))

    # EDIT: re-render the whole group message when any member changed enough
    edited_messages: set[int | None] = set()
    for d in active:
        rec = records.get(d.key)
        if rec is None or rec.state != "active" or rec.message_id in edited_messages:
            continue
        severity_changed = d.severity_class != rec.severity_class
        top_changed = set(tops(d)) != set(rec.top_ids) and now - rec.last_sent_at >= cooldown
        if severity_changed or top_changed:
            group = [by_key[k] for k in rec.group if k in by_key and by_key[k].resolved_at is None]
            actions.append(EditExisting(
                message_id=rec.message_id,
                keys=tuple(g.key for g in group),
                text=render(group, ctx, now, p),
                top_ids={g.key: tops(g) for g in group},
                severity={g.key: g.severity_class for g in group},
            ))
            edited_messages.add(rec.message_id)

    # RESOLVED: only after the merge window, so a flapping status doesn't send "all clear" then "new"
    for key, rec in records.items():
        if rec.state != "active":
            continue
        d = by_key.get(key)
        gone_long_enough = d is None or (d.resolved_at is not None and now - d.resolved_at >= cooldown)
        if gone_long_enough:
            since = f", since {london_hhmm(d.resolved_at)}" if d is not None and d.resolved_at else ""
            line_name = d.line_name if d is not None else key.split(":")[0].replace("-", " ").title()
            actions.append(SendResolved(key, rec.message_id, f"✅ {line_name} line: back to good service{since}."))
    return actions


def apply(records: Mapping[str, AlertRecord], action: Action, message_id: int | None, now: datetime) -> dict[str, AlertRecord]:
    """New records after an action was successfully sent (message_id from Telegram for SendNew)."""
    out = dict(records)
    if isinstance(action, SendNew):
        for key in action.keys:
            out[key] = AlertRecord(key, action.keys, message_id, "active", action.severity[key], action.top_ids[key], now)
    elif isinstance(action, EditExisting):
        for key in action.keys:
            out[key] = replace(out[key], severity_class=action.severity[key], top_ids=action.top_ids[key], last_sent_at=now)
    elif isinstance(action, SendResolved):
        out[action.key] = replace(out[action.key], state="resolved", last_sent_at=now)
    return out


def prune(records: Mapping[str, AlertRecord], now: datetime, keep: timedelta = timedelta(days=2)) -> dict[str, AlertRecord]:
    """Forget resolved records after a while so the state document stays small."""
    return {k: r for k, r in records.items() if r.state == "active" or now - r.last_sent_at < keep}

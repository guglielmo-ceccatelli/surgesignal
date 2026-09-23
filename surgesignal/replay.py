"""Replay recorded TfL status through the real alerter on a simulated clock.

    raw/status/<YYYY-MM-DD>/<HHMMSS>Z.json.gz   (change-only: each file holds until the next)
        │
        ▼  every `step` from the first snapshot to the last + `tail`:
    fetch() returns the latest snapshot at or before the tick ─► Alerter.tick(tick)
        │                                                         (parser, tracker, engine, lifecycle)
        ▼
    RecordingTelegram: every message, edit and all-clear the dispatcher would have received

Uses: end-to-end tests on recorded data (T7), counting qualifying events (T8), and demos
("what would SurgeSignal have sent during Tuesday's closure").
"""

from __future__ import annotations

import gzip
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from surgesignal.alerts.settings import Settings
from surgesignal.config import Config
from surgesignal.engine.params import Params
from surgesignal.store.state import FileStore
from surgesignal.tfl.network import Network

DISPATCHER, BUILDER = 1, 2


class RecordingTelegram:
    """Stands in for Telegram: records instead of sending. send() returns increasing message ids."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.answers: list[tuple[str, str | None]] = []
        self.pending_updates: list[dict[str, Any]] = []
        self._next_id = 100

    def send(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None,
             reply_to: int | None = None) -> int:
        self._next_id += 1
        self.sent.append({"chat": chat_id, "text": text, "markup": reply_markup, "reply_to": reply_to, "id": self._next_id})
        return self._next_id

    def edit(self, chat_id: int, message_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        self.edits.append({"chat": chat_id, "id": message_id, "text": text, "markup": reply_markup})

    def updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        out, self.pending_updates = self.pending_updates, []
        return out

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.answers.append((callback_id, text))


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str  # "new" | "edit" | "resolved" | "problem" | "error"
    text: str


@dataclass
class ReplayResult:
    events: list[Event] = field(default_factory=list)
    ticks: int = 0

    def of(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]


def load_snapshots(root: Path) -> list[tuple[datetime, bytes]]:
    """Read <root>/<YYYY-MM-DD>/<HHMMSS>Z.json.gz files, oldest first."""
    out = []
    for path in Path(root).glob("*/*Z.json.gz"):
        at = datetime.strptime(f"{path.parent.name} {path.name[:6]}", "%Y-%m-%d %H%M%S").replace(tzinfo=timezone.utc)
        out.append((at, gzip.decompress(path.read_bytes())))
    return sorted(out, key=lambda s: s[0])


def replay(snapshots: Sequence[tuple[datetime, bytes]], network: Network, params: Params, settings: Settings,
           step: timedelta = timedelta(minutes=1), tail: timedelta = timedelta(minutes=30),
           restart_at: Iterable[datetime] = (), state_dir: Path | None = None) -> ReplayResult:
    """Run the alerter over recorded snapshots. `restart_at` rebuilds the alerter at those
    times (same stored state), as a crash + auto-restart would."""
    from workers.alerter import Alerter  # workers depend on surgesignal, not the other way round

    if not snapshots:
        return ReplayResult()
    snapshots = sorted(snapshots, key=lambda s: s[0])
    tmp = None
    if state_dir is None:
        tmp = tempfile.TemporaryDirectory()
        state_dir = Path(tmp.name)
    store = FileStore(state_dir)
    store.put_state("settings", settings.to_dict())
    cfg = Config("replay", dispatcher_chat_id=DISPATCHER, builder_chat_id=BUILDER)
    tg = RecordingTelegram()
    current = {"payload": snapshots[0][1]}
    alerter = Alerter(cfg, store, tg, network, params, fetch=lambda url: current["payload"])

    result = ReplayResult()
    restarts = sorted(restart_at)
    t, end, i = snapshots[0][0], snapshots[-1][0] + tail, 0
    try:
        while t <= end:
            while i + 1 < len(snapshots) and snapshots[i + 1][0] <= t:
                i += 1
            current["payload"] = snapshots[i][1]
            if restarts and restarts[0] <= t:
                restarts.pop(0)
                alerter = Alerter(cfg, store, tg, network, params, fetch=lambda url: current["payload"])
            n_sent, n_edits = len(tg.sent), len(tg.edits)
            try:
                alerter.tick(t)
            except Exception as exc:  # the live loop reports and carries on; so does the replay
                result.events.append(Event(t, "error", repr(exc)))
            for m in tg.sent[n_sent:]:
                kind = "problem" if m["chat"] == BUILDER else ("resolved" if m["reply_to"] else "new")
                result.events.append(Event(t, kind, m["text"]))
            for e in tg.edits[n_edits:]:
                result.events.append(Event(t, "edit", e["text"]))
            result.ticks += 1
            t += step
    finally:
        if tmp is not None:
            tmp.cleanup()
    return result

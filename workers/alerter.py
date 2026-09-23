"""Alerter: every 60 s turn live TfL status into dispatcher alerts; in between, answer Telegram.

Runs as its own process, separate from the logger (eng review 1A). It polls TfL itself, so the
logger stays a dumb recorder and neither process depends on the other being up.

    every 60 s:  TfL status ─► parse ─► tracker (t0 / resolved) ─► engine @ now+ramp ─► lifecycle.decide
                     │            │                                            │
                     │            └─► problems ─► builder chat (once a day)    ▼
                     └─► store: tracker, disruptions_current        send / edit / resolve
                                                                     ─► store: alerts (after EACH send)
    otherwise:   Telegram long-poll (25 s) ─► Bot.handle (commands, check-in buttons)

A failed send raises before its record is saved, so the next tick retries it. Records are
saved after every successful send, so a crash mid-tick never re-sends what already went out.

Usage:  python -m workers.alerter           (needs config.local.yaml, see config.example.yaml)
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from surgesignal.alerts import lifecycle
from surgesignal.alerts.bot import CHECKIN_WINDOW, Bot
from surgesignal.alerts.lifecycle import AlertRecord, Context
from surgesignal.alerts.settings import Settings
from surgesignal.alerts.telegram import Telegram
from surgesignal.config import Config, load_config
from surgesignal.engine import model
from surgesignal.engine.geo import in_service_area
from surgesignal.engine.params import Params, load_params
from surgesignal.engine.types import Conditions, Disruption, ServiceArea
from surgesignal.store.state import FileStore, Store, SupabaseStore
from surgesignal.tfl.network import Network, load_network
from surgesignal.tfl.parser import parse_status
from surgesignal.tfl.tracker import IncidentTracker
from workers.logger import http_fetch, log, tfl_url

STATUS_PATH = "/Line/Mode/tube,elizabeth-line/Status"
TICK_SECONDS = 60
POLL_SECONDS = 25
ERROR_PING_EVERY = timedelta(hours=1)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def disruption_row(d: Disruption) -> dict[str, Any]:
    return {"key": d.key, "line": d.line, "line_name": d.line_name, "severity_class": d.severity_class,
            "is_unplanned": d.is_unplanned, "station_ids": list(d.affected_station_ids), "t0": d.t0.isoformat(),
            "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
            "line_wide": d.line_wide, "area_uncertain": d.area_uncertain}


class Alerter:
    def __init__(self, cfg: Config, store: Store, tg: Telegram, network: Network, params: Params,
                 fetch=http_fetch) -> None:
        self.cfg, self.store, self.tg, self.network, self.p, self.fetch = cfg, store, tg, network, params, fetch
        self.line_stations = {lid: line.station_ids for lid, line in network.lines.items()}
        # Interim: uniform relative busyness until the TfL entry/exit baseline is built (T9).
        self.baseline = {sid: 1.0 for sid in network.stations}
        self._last_error_ping: dict[str, datetime] = {}

    def tick(self, now: datetime) -> list[lifecycle.Action]:
        status = json.loads(self.fetch(tfl_url(STATUS_PATH, None)))
        parsed = parse_status(status, self.network)

        tracker = IncidentTracker.from_dict(self.p, self.store.get_state("tracker") or {})
        disruptions = tracker.update(parsed.incidents, now)
        self.store.put_state("tracker", tracker.to_dict())
        self.store.put_disruptions([disruption_row(d) for d in disruptions])
        self._report_problems(parsed.problems, now)

        settings = Settings.from_dict(self.store.get_state("settings"))
        if not settings.has_area or self.cfg.dispatcher_chat_id is None:
            return []
        area = ServiceArea(settings.area_lat, settings.area_lon, settings.radius_km)
        # The alert is a forecast: score where demand is heading (end of the ramp), not where it is
        # this second, or a fresh disruption would only cross the threshold minutes after we saw it.
        forecast_at = now + timedelta(minutes=self.p.ramp_min)
        result = model.run(self.network.stations, disruptions, Conditions(), self.baseline, area, forecast_at, self.p)
        dead = frozenset(c.station_id for c in self.store.checkins_since(now - CHECKIN_WINDOW) if c.status == "dead")
        ctx = Context(
            result=result,
            line_stations=self.line_stations,
            stations_in_area=frozenset(s.id for s in self.network.stations.values() if in_service_area(s, area)),
            idle_drivers=settings.idle_drivers,
            suppressed_station_ids=dead,
            quiet=settings.quiet,
        )

        records = {k: AlertRecord.from_dict(v) for k, v in (self.store.get_state("alerts") or {}).items()}
        actions = lifecycle.decide(disruptions, records, ctx, now, self.p)
        for action in actions:
            message_id = self._execute(action)
            records = lifecycle.apply(records, action, message_id, now)
            self._save_records(records)
        self._save_records(lifecycle.prune(records, now))
        return actions

    def _execute(self, action: lifecycle.Action) -> int | None:
        chat = self.cfg.dispatcher_chat_id
        if isinstance(action, lifecycle.SendNew):
            return self.tg.send(chat, action.text)
        if isinstance(action, lifecycle.EditExisting):
            if action.message_id is not None:
                self.tg.edit(chat, action.message_id, action.text)
            return action.message_id
        return self.tg.send(chat, action.text, reply_to=action.reply_to)

    def _save_records(self, records: dict[str, AlertRecord]) -> None:
        self.store.put_state("alerts", {k: r.to_dict() for k, r in records.items()})

    def _report_problems(self, problems: list[str], now: datetime) -> None:
        """Parser problems go to the builder once per day each, never silently (eng review 5A)."""
        if not problems:
            return
        sent = self.store.get_state("problems") or {}
        today = now.date().isoformat()
        new = [p for p in problems if sent.get(p) != today]
        for p in new:
            log(f"parser problem: {p}")
        if new and self.cfg.builder_chat_id is not None:
            self.tg.send(self.cfg.builder_chat_id, "SurgeSignal parser problems:\n" + "\n".join(f"• {p}" for p in new))
        if new:
            self.store.put_state("problems", {**{k: v for k, v in sent.items() if v == today}, **{p: today for p in new}})

    def report_error(self, exc: Exception, now: datetime) -> None:
        kind = type(exc).__name__
        log(f"tick failed: {exc!r}")
        last = self._last_error_ping.get(kind)
        if self.cfg.builder_chat_id is None or (last and now - last < ERROR_PING_EVERY):
            return
        self._last_error_ping[kind] = now
        try:
            self.tg.send(self.cfg.builder_chat_id, f"SurgeSignal alerter error: {exc!r}")
        except Exception as send_exc:  # Telegram itself may be the problem
            log(f"could not report error: {send_exc!r}")


def make_store(cfg: Config) -> Store:
    if cfg.supabase_url and cfg.supabase_service_key:
        return SupabaseStore(cfg.supabase_url, cfg.supabase_service_key)
    log("Supabase not configured: using local files in data/state")
    return FileStore(DATA_DIR / "state")


def write_heartbeat(path: Path, loop: datetime, tick_ok: datetime | None) -> None:
    """{"loop": last loop pass (process alive), "tick_ok": last successful tick}; read by the watchdog."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"loop": loop.isoformat(), "tick_ok": tick_ok.isoformat() if tick_ok else None}))
    tmp.replace(path)


def run_forever(alerter: Alerter, bot: Bot, tg: Telegram, store: Store, iterations: int | None = None,
                heartbeat_path: Path = DATA_DIR / "alerter_heartbeat.json") -> None:
    """Main loop. `iterations` bounds it for tests; None runs until interrupted."""
    last_tick = float("-inf")
    tick_ok: datetime | None = None
    while iterations is None or iterations > 0:
        if iterations is not None:
            iterations -= 1
        if time.monotonic() - last_tick >= TICK_SECONDS:
            last_tick = time.monotonic()
            now = datetime.now(timezone.utc)
            try:
                for a in alerter.tick(now):
                    log(f"sent {type(a).__name__}")
                tick_ok = now
            except Exception as exc:  # keep running; report, retry next tick
                alerter.report_error(exc, now)
        write_heartbeat(heartbeat_path, datetime.now(timezone.utc), tick_ok)
        try:
            offset = (store.get_state("telegram") or {}).get("offset")
            for update in tg.updates(offset, POLL_SECONDS):
                try:
                    bot.handle(update, datetime.now(timezone.utc))
                except Exception as exc:
                    log(f"update {update.get('update_id')} failed: {exc!r}")
                store.put_state("telegram", {"offset": update["update_id"] + 1})
        except Exception as exc:
            log(f"telegram poll failed: {exc!r}")
            time.sleep(5)


def main() -> int:
    cfg = load_config()
    store = make_store(cfg)
    tg = Telegram(cfg.telegram_bot_token)
    network, params = load_network(), load_params()
    alerter, bot = Alerter(cfg, store, tg, network, params), Bot(cfg, store, tg, network)
    log("alerter started" + ("" if cfg.dispatcher_chat_id else " (no dispatcher_chat_id yet: send /whoami to the bot)"))
    try:
        run_forever(alerter, bot, tg, store)
    except KeyboardInterrupt:
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

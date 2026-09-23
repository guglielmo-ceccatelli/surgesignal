"""Telegram commands and buttons. Only allowlisted chats are answered (eng review 2A).

    anyone       /whoami                  → your chat id (so it can be added to config)
    dispatcher   /start /help /settings /status
                 /idle N   /area <station|postcode> [km]   /quiet HH:MM-HH:MM|off
    dispatcher,  /checkin → buttons: stations of the current alert
    driver                  → buttons: busy / normal / dead → saved (hashed driver id)
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from surgesignal.alerts.format import london_hhmm
from surgesignal.alerts.lifecycle import AlertRecord
from surgesignal.alerts.settings import Settings, SettingsError, postcodes_io, set_area, set_idle, set_quiet
from surgesignal.alerts.telegram import Telegram, keyboard
from surgesignal.config import Config
from surgesignal.engine.explain import SEVERITY_WORDS
from surgesignal.store.state import CHECKIN_STATUSES, Checkin, Store
from surgesignal.tfl.network import Network

CHECKIN_WINDOW = timedelta(minutes=30)
MAX_CHECKIN_STATIONS = 5

HELP_DISPATCHER = """SurgeSignal alerts you when a Tube or Elizabeth line disruption is about to push booking demand up near your area.

/area Clapham Common 5 — centre and radius (km), or a postcode: /area SW4 7AA 3
/idle 6 — cars usually free (for "move N cars")
/quiet 23:00-06:00 — no alerts overnight (or /quiet off)
/settings — show settings
/status — what's happening now
/checkin — report how busy a station really is"""

HELP_DRIVER = "SurgeSignal: when an alert is active, send /checkin to report how busy a station really is."


def driver_hash(salt: str, chat_id: int) -> str:
    return hashlib.sha256(f"{salt}:{chat_id}".encode()).hexdigest()[:16]


class Bot:
    def __init__(self, cfg: Config, store: Store, tg: Telegram, network: Network, postcode_lookup=postcodes_io) -> None:
        self.cfg, self.store, self.tg, self.network = cfg, store, tg, network
        self.postcode_lookup = postcode_lookup

    # ---- entry point ----------------------------------------------------------------

    def handle(self, update: dict[str, Any], now: datetime) -> None:
        if "callback_query" in update:
            self._callback(update["callback_query"], now)
            return
        msg = update.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if chat is None or not text.startswith("/"):
            return
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd == "/whoami":
            self.tg.send(chat, f"Your chat id is {chat}. Add it to config.local.yaml to use SurgeSignal.")
            return
        role = self.cfg.role(chat)
        if role is None:
            return  # not on the allowlist: ignore
        if cmd in ("/start", "/help"):
            self.tg.send(chat, HELP_DISPATCHER if role == "dispatcher" else HELP_DRIVER)
        elif cmd == "/checkin":
            self._checkin_menu(chat)
        elif role != "dispatcher":
            self.tg.send(chat, HELP_DRIVER)
        elif cmd == "/settings":
            self.tg.send(chat, self._settings().describe())
        elif cmd == "/status":
            self.tg.send(chat, self._status(now))
        elif cmd in ("/idle", "/area", "/quiet"):
            self._update_settings(chat, cmd, arg)
        else:
            self.tg.send(chat, HELP_DISPATCHER)

    # ---- settings -------------------------------------------------------------------

    def _settings(self) -> Settings:
        return Settings.from_dict(self.store.get_state("settings"))

    def _update_settings(self, chat: int, cmd: str, arg: str) -> None:
        s = self._settings()
        try:
            if cmd == "/idle":
                s = set_idle(s, arg)
            elif cmd == "/area":
                s = set_area(s, arg, self.network, self.postcode_lookup)
            else:
                s = set_quiet(s, arg)
        except SettingsError as exc:
            self.tg.send(chat, str(exc))
            return
        self.store.put_state("settings", s.to_dict())
        self.tg.send(chat, "Saved.\n" + s.describe())

    # ---- status ---------------------------------------------------------------------

    def _status(self, now: datetime) -> str:
        s = self._settings()
        lines = [s.describe(), ""]
        tracker = self.store.get_state("tracker") or {}
        active = [v["incident"] for v in tracker.values() if not v.get("resolved_at")]
        if active:
            lines.append("Disruptions now:")
            for inc in sorted(active, key=lambda i: i["line_name"]):
                where = "whole line" if inc["line_wide"] else f"{len(inc['station_ids'])} stations"
                lines.append(f"• {inc['line_name']} line {SEVERITY_WORDS[inc['severity_class']]} ({where})")
        else:
            lines.append("No Tube or Elizabeth line disruptions right now.")
        recent = self.store.checkins_since(now - CHECKIN_WINDOW)
        if recent:
            lines += ["", "Check-ins (last 30 min):"]
            for c in recent[-8:]:
                name = self.network.stations[c.station_id].name if c.station_id in self.network.stations else c.station_id
                lines.append(f"• {name}: {c.status} ({london_hhmm(c.ts)})")
        return "\n".join(lines)

    # ---- check-ins ------------------------------------------------------------------

    def _alert_station_ids(self) -> list[str]:
        records = [AlertRecord.from_dict(r) for r in (self.store.get_state("alerts") or {}).values()]
        ids: list[str] = []
        for rec in sorted(records, key=lambda r: r.last_sent_at, reverse=True):
            if rec.state == "active":
                ids += [s for s in rec.top_ids if s not in ids]
        return ids[:MAX_CHECKIN_STATIONS]

    def _checkin_menu(self, chat: int) -> None:
        ids = [s for s in self._alert_station_ids() if s in self.network.stations]
        if not ids:
            self.tg.send(chat, "No active alert right now, so there's nothing to check in for.")
            return
        rows = [[(self.network.stations[s].name, f"ci|{s}")] for s in ids]
        self.tg.send(chat, "Which station are you at?", reply_markup=keyboard(rows))

    def _callback(self, cq: dict[str, Any], now: datetime) -> None:
        chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
        message_id = (cq.get("message") or {}).get("message_id")
        data = cq.get("data") or ""
        if chat is None or self.cfg.role(chat) is None:
            self.tg.answer_callback(cq["id"])
            return
        parts = data.split("|")
        if parts[0] == "ci" and len(parts) == 2 and parts[1] in self.network.stations:
            sid = parts[1]
            name = self.network.stations[sid].name
            rows = [[(label.capitalize(), f"cs|{sid}|{label}") for label in CHECKIN_STATUSES]]
            self.tg.edit(chat, message_id, f"How busy is {name}?", reply_markup=keyboard(rows))
            self.tg.answer_callback(cq["id"])
        elif parts[0] == "cs" and len(parts) == 3 and parts[1] in self.network.stations and parts[2] in CHECKIN_STATUSES:
            sid, status = parts[1], parts[2]
            self.store.add_checkin(Checkin(sid, status, now, driver_hash(self.cfg.driver_hash_salt, chat)))
            name = self.network.stations[sid].name
            self.tg.edit(chat, message_id, f"Thanks: {name} {status} ({london_hhmm(now)}).")
            self.tg.answer_callback(cq["id"], "Saved")
        else:
            self.tg.answer_callback(cq["id"], "That button has expired.")

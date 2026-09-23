"""Watchdog: launchd runs this every 5 minutes. It reads the logger and alerter heartbeats,
restarts a process that has hung, and tells the builder on Telegram, once per problem.

    heartbeat.json          {"loop": t, "feeds": {status: t, disruption: t, bikepoint: t}}
    alerter_heartbeat.json  {"loop": t, "tick_ok": t}
                │
                ▼
    check()  (pure) ─► problems now:  "logger" / "alerter"  loop older than PROCESS_STALE  → restart + ping
                                      "feed:<name>"          feed older than its limit, loop fresh → ping only
                     ─► compare with problems already reported ─► new → "⚠️ …", cleared → "✅ … back to normal"

The Mac sleeping makes every heartbeat stale at once without anything being wrong. So a run
that starts more than SLEEP_GAP after the previous one (or the very first run) only records
the time and checks nothing; the processes get a round to catch up.

Usage:  python -m workers.watchdog          (normally via launchd: scripts/launchd.py install)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from surgesignal.alerts.format import london_hhmm
from workers.logger import FEEDS, log

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROCESS_STALE = timedelta(minutes=10)
SLEEP_GAP = timedelta(minutes=15)
RESTART_EVERY = timedelta(minutes=15)  # at most one restart per process per 15 min
FEED_STALE = {f.name: max(timedelta(minutes=10), timedelta(seconds=3 * f.interval_s)) for f in FEEDS}
LABELS = {"logger": "com.surgesignal.logger", "alerter": "com.surgesignal.alerter"}


@dataclass(frozen=True)
class Verdict:
    messages: list[str] = field(default_factory=list)
    restart: list[str] = field(default_factory=list)  # "logger" / "alerter"
    state: dict[str, Any] = field(default_factory=dict)


def _t(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _problems(now: datetime, logger_hb: dict[str, Any] | None, alerter_hb: dict[str, Any] | None) -> dict[str, str]:
    """component -> human description of what's wrong (empty dict = all healthy)."""
    found: dict[str, str] = {}
    logger_loop = _t((logger_hb or {}).get("loop"))
    if logger_loop is None or now - logger_loop > PROCESS_STALE:
        seen = f"last seen {london_hhmm(logger_loop)}" if logger_loop else "no heartbeat"
        found["logger"] = f"logger stopped responding ({seen})"
    else:
        feeds = (logger_hb or {}).get("feeds", {})
        for name, limit in FEED_STALE.items():
            last = _t(feeds.get(name))
            if last is None or now - last > limit:
                since = f"since {london_hhmm(last)}" if last else "yet"
                found[f"feed:{name}"] = f"TfL {name} not fetched {since}: logger is running, so TfL or the network is failing"
    alerter_loop = _t((alerter_hb or {}).get("loop"))
    if alerter_loop is None or now - alerter_loop > PROCESS_STALE:
        seen = f"last seen {london_hhmm(alerter_loop)}" if alerter_loop else "no heartbeat"
        found["alerter"] = f"alerter stopped responding ({seen})"
    return found


def check(now: datetime, logger_hb: dict[str, Any] | None, alerter_hb: dict[str, Any] | None,
          state: dict[str, Any]) -> Verdict:
    last_run = _t(state.get("last_run"))
    reported: dict[str, str] = dict(state.get("reported", {}))
    restarted: dict[str, str] = dict(state.get("restarted", {}))
    new_state = {"last_run": now.isoformat(), "reported": reported, "restarted": restarted}
    if last_run is None or now - last_run > SLEEP_GAP:
        return Verdict(state=new_state)  # first run or just woke up: give processes a round

    problems = _problems(now, logger_hb, alerter_hb)
    messages, restart = [], []
    for component, text in problems.items():
        if component in LABELS:
            last_restart = _t(restarted.get(component))
            if last_restart is None or now - last_restart >= RESTART_EVERY:
                restart.append(component)
                restarted[component] = now.isoformat()
        if component not in reported:
            action = " Restarting it." if component in restart else ""
            backup = " GitHub keeps backing up status and disruptions." if component == "logger" else ""
            messages.append(f"⚠️ SurgeSignal {text}.{action}{backup}")
            reported[component] = now.isoformat()
    for component in [c for c in reported if c not in problems]:
        messages.append(f"✅ SurgeSignal {component.replace('feed:', 'TfL ')} back to normal.")
        del reported[component]
    return Verdict(messages, restart, new_state)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def restart_job(component: str) -> None:
    label = LABELS[component]
    result = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        log(f"watchdog: could not restart {label}: {result.stderr.strip() or result.returncode}")


def main(data_dir: Path = DATA_DIR) -> int:
    now = datetime.now(timezone.utc)
    state_path = data_dir / "watchdog.json"
    verdict = check(now, _read(data_dir / "heartbeat.json"), _read(data_dir / "alerter_heartbeat.json"),
                    _read(state_path) or {})
    for component in verdict.restart:
        restart_job(component)
    for msg in verdict.messages:
        log(f"watchdog: {msg}")
    if verdict.messages:
        _notify(verdict.messages)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(verdict.state, indent=1))
    tmp.replace(state_path)
    return 0


def _notify(messages: list[str]) -> None:
    from surgesignal.alerts.telegram import Telegram
    from surgesignal.config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as exc:
        log(f"watchdog: no Telegram config ({exc}); messages only logged")
        return
    if cfg.builder_chat_id is None:
        return
    try:
        Telegram(cfg.telegram_bot_token).send(cfg.builder_chat_id, "\n".join(messages))
    except Exception as exc:  # network down is exactly when this may fail; the log still has it
        log(f"watchdog: Telegram send failed: {exc!r}")


if __name__ == "__main__":
    sys.exit(main())

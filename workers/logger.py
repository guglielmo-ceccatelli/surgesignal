"""Raw TfL logger: polls TfL feeds and stores every changed response, gzipped, on local disk.

Runs as its own long-lived process, separate from the alerter/bot, so a bot crash never
stops data collection (eng review 1A). It never exits on a network or API error: a failed
poll is logged and retried on the next tick.

    every 15 s tick:
      for feed in FEEDS whose interval has elapsed:
          fetch ──ok──► RawLog.save_if_changed ──► heartbeat[feed] = now
                └─err─► log to stderr, heartbeat unchanged (staleness is visible)
      write <data>/heartbeat.json  {"loop": now, "feeds": {feed: last success}}  (read by workers/watchdog.py)

Usage:
    python -m workers.logger                 # run forever, data in Surge/data/raw
    python -m workers.logger --once          # one poll of every feed, then exit
Optional env: TFL_APP_KEY (raises TfL's anonymous rate limit).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from surgesignal.store.rawlog import RawLog

TFL_BASE = "https://api.tfl.gov.uk"
TICK_SECONDS = 15
FETCH_TIMEOUT_SECONDS = 20
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Feed:
    name: str
    path: str
    interval_s: int


FEEDS = (
    Feed("status", "/Line/Mode/tube,elizabeth-line/Status", 60),
    Feed("disruption", "/Line/Mode/tube,elizabeth-line/Disruption", 60),
    Feed("bikepoint", "/BikePoint", 300),
)

Fetcher = Callable[[str], bytes]


def tfl_url(path: str, app_key: str | None) -> str:
    url = TFL_BASE + path
    if app_key:
        url += "?" + urllib.parse.urlencode({"app_key": app_key})
    return url


def http_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "SurgeSignal-logger/0.1"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
        return resp.read()


class Logger:
    def __init__(self, data_dir: Path, fetch: Fetcher = http_fetch, app_key: str | None = None,
                 feeds: tuple[Feed, ...] = FEEDS) -> None:
        self.data_dir = Path(data_dir)
        self.fetch = fetch
        self.app_key = app_key
        self.feeds = feeds
        self.logs = {f.name: RawLog(self.data_dir / "raw", f.name) for f in feeds}
        self.last_attempt: dict[str, float] = {}
        self.heartbeat_path = self.data_dir / "heartbeat.json"
        self.heartbeat: dict[str, str] = {}  # feed -> last successful poll (UTC ISO)
        if self.heartbeat_path.exists():
            saved = json.loads(self.heartbeat_path.read_text())
            self.heartbeat = saved.get("feeds", {}) if "feeds" in saved else saved  # older flat format

    def poll_due(self, now_monotonic: float, now_utc: datetime) -> None:
        """Poll every feed whose interval has elapsed. Never raises for a single feed's failure."""
        for feed in self.feeds:
            last = self.last_attempt.get(feed.name)
            if last is not None and now_monotonic - last < feed.interval_s:
                continue
            self.last_attempt[feed.name] = now_monotonic
            self._poll(feed, now_utc)
        self._write_heartbeat(now_utc)

    def _poll(self, feed: Feed, now_utc: datetime) -> None:
        try:
            payload = self.fetch(tfl_url(feed.path, self.app_key))
            saved = self.logs[feed.name].save_if_changed(payload, now_utc)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log(f"{feed.name}: poll failed: {exc!r}")
            return
        self.heartbeat[feed.name] = now_utc.isoformat()
        if saved:
            log(f"{feed.name}: saved {saved.relative_to(self.data_dir)}")

    def _write_heartbeat(self, now_utc: datetime) -> None:
        """{"loop": last tick (process alive), "feeds": {feed: last success}}; read by the watchdog."""
        tmp = self.heartbeat_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"loop": now_utc.isoformat(), "feeds": self.heartbeat}, indent=2, sort_keys=True))
        os.replace(tmp, self.heartbeat_path)


def select_feeds(spec: str) -> tuple[Feed, ...]:
    by_name = {f.name: f for f in FEEDS}
    names = [n.strip() for n in spec.split(",") if n.strip()]
    unknown = [n for n in names if n not in by_name]
    if unknown or not names:
        raise SystemExit(f"unknown or empty --feeds {unknown or spec!r}; choose from {', '.join(by_name)}")
    return tuple(by_name[n] for n in names)


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--once", action="store_true", help="poll every feed once, then exit")
    parser.add_argument("--feeds", default=",".join(f.name for f in FEEDS),
                        help="comma-separated subset of: " + ", ".join(f.name for f in FEEDS))
    args = parser.parse_args(argv)

    feeds = select_feeds(args.feeds)
    logger = Logger(args.data_dir, app_key=os.environ.get("TFL_APP_KEY") or None, feeds=feeds)
    log(f"logging {', '.join(f.name for f in logger.feeds)} to {args.data_dir / 'raw'}")
    try:
        while True:
            logger.poll_due(time.monotonic(), datetime.now(timezone.utc))
            if args.once:
                return 0
            time.sleep(TICK_SECONDS)
    except KeyboardInterrupt:
        log("stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())

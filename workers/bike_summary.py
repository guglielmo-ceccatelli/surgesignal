"""Fetch /BikePoint once and save the per-station summary as <out>/<YYYY-MM-DD>/<HHMMSS>Z.csv.gz.

    python -m workers.bike_summary --out databranch/bikes      # what GitHub Actions runs every 5 min

Raw responses are ~2 MB; each summary is a few KB gzipped, so weeks of them fit in git.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from surgesignal.inputs.bikes import summarise, to_csv
from surgesignal.tfl.network import load_network
from workers.logger import http_fetch, log, tfl_url


def write_summary(payload: bytes, out: Path, now: datetime) -> Path:
    rows = summarise(json.loads(payload), load_network().stations)
    if not rows:
        raise ValueError("BikePoint response had no usable docks")
    day = out / now.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"{now.strftime('%H%M%S')}Z.csv.gz"
    path.write_bytes(gzip.compress(to_csv(rows).encode()))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    try:
        payload = http_fetch(tfl_url("/BikePoint", os.environ.get("TFL_APP_KEY") or None))
        path = write_summary(payload, args.out, now)
    except Exception as exc:  # a missed snapshot is fine; the next run in 5 minutes tries again
        log(f"bike summary failed: {exc!r}")
        return 1
    log(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

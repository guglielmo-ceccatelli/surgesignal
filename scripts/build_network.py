"""Build surgesignal/data/network.json from TfL Route/Sequence responses.

    python -m scripts.build_network --fetch            # download all lines, then build
    python -m scripts.build_network --from data/tfl/route_sequence   # rebuild from saved JSON

Re-run when TfL changes a route (new station, new branch). The raw responses are saved to
data/tfl/route_sequence/ (git-ignored); the compact network.json is committed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from surgesignal.tfl.network import DEFAULT_PATH, build_network
from workers.logger import http_fetch, tfl_url

LINES = (
    "bakerloo", "central", "circle", "district", "elizabeth", "hammersmith-city",
    "jubilee", "metropolitan", "northern", "piccadilly", "victoria", "waterloo-city",
)
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "tfl" / "route_sequence"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--from", dest="src", type=Path, default=RAW_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(argv)

    if args.fetch:
        args.src.mkdir(parents=True, exist_ok=True)
        for line in LINES:
            (args.src / f"{line}.json").write_bytes(http_fetch(tfl_url(f"/Line/{line}/Route/Sequence/outbound", None)))

    files = sorted(args.src.glob("*.json"))
    missing = sorted(set(LINES) - {f.stem for f in files})
    if missing:
        print(f"missing route sequences for: {', '.join(missing)} (use --fetch)", file=sys.stderr)
        return 1
    network = build_network(json.loads(f.read_text()) for f in files)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(network.to_json(), indent=1, ensure_ascii=False) + "\n")
    print(f"{len(network.stations)} stations, {len(network.lines)} lines -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

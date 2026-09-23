"""What would SurgeSignal have sent? Replays recorded TfL status through the real alerter.

    python -m scripts.replay                                   # all of data/raw/status, Clapham Common 5 km
    python -m scripts.replay --src backup/raw/status --date 2026-09-23
    python -m scripts.replay --area "Leytonstone" --radius 3 --idle 4

Prints each alert, edit and all-clear with its London time, then a count of incidents seen vs alerted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from surgesignal.alerts.format import london_hhmm
from surgesignal.alerts.settings import Settings, SettingsError, set_area, set_idle
from surgesignal.engine.params import load_params
from surgesignal.replay import load_snapshots, replay
from surgesignal.tfl.network import load_network
from surgesignal.tfl.parser import parse_status

REPO = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", type=Path, default=REPO / "data" / "raw" / "status")
    parser.add_argument("--date", help="only this day (YYYY-MM-DD, UTC folder name)")
    parser.add_argument("--area", default="Clapham Common")
    parser.add_argument("--radius", type=float, default=5.0)
    parser.add_argument("--idle", type=int, default=6)
    args = parser.parse_args(argv)

    network, params = load_network(), load_params()
    try:
        settings = set_idle(set_area(Settings(), f"{args.area} {args.radius:g}", network), str(args.idle))
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 1
    snapshots = load_snapshots(args.src)
    if args.date:
        snapshots = [s for s in snapshots if s[0].date().isoformat() == args.date]
    if not snapshots:
        print(f"no snapshots in {args.src}" + (f" for {args.date}" if args.date else ""), file=sys.stderr)
        return 1

    incidents = {inc.key: inc for _, raw in snapshots for inc in parse_status(json.loads(raw), network).incidents}
    result = replay(snapshots, network, params, settings)
    print(f"{len(snapshots)} snapshots, {snapshots[0][0]:%Y-%m-%d %H:%M}–{snapshots[-1][0]:%H:%M} UTC, "
          f"area {settings.area_label} {settings.radius_km:g} km, {settings.idle_drivers} idle cars\n")
    for e in result.events:
        print(f"[{london_hhmm(e.at)} {e.kind}]\n{e.text}\n")
    lines = ", ".join(sorted({i.line_name for i in incidents.values()})) or "none"
    print(f"incidents seen: {len(incidents)} ({lines}); alert messages: {len(result.of('new'))}; "
          f"edits: {len(result.of('edit'))}; all-clears: {len(result.of('resolved'))}")
    if incidents and not result.of("new"):
        print("No alerts: every incident was below the alert threshold or outside the area.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

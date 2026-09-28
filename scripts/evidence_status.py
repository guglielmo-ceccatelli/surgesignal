"""The week-one count (T8): how often would a dispatcher get an alert? Writes the numbers the
public site shows (surgesignal/data/evidence.json) so it doesn't need the logs.

    python -m scripts.evidence_status              # all local logs
    python -m scripts.evidence_status --dry-run    # print only

Sample areas are five dispatchers' patches across London (5 km each), plus one covering the
whole network, whose alert count is the "qualifying disruptions" figure.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from surgesignal.alerts.format import LONDON
from surgesignal.alerts.settings import Settings
from surgesignal.console import station_by_name
from surgesignal.engine.params import load_params
from surgesignal.evidence import AreaRate, WeekCount, alerts_in, covered_segments, open_hours, rate
from surgesignal.replay import load_snapshots
from surgesignal.tfl.network import load_network

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw"
OUT = REPO / "surgesignal" / "data" / "evidence.json"
AREAS = [("Clapham Common", 5.0), ("Stratford (London)", 5.0), ("Putney Bridge", 5.0),
         ("King's Cross St. Pancras", 5.0), ("Ealing Broadway", 5.0)]
WHOLE_NETWORK = ("Charing Cross", 40.0)
IDLE = 6


def snapshot_times(root: Path) -> list[datetime]:
    """Timestamps from file names, without reading the files."""
    return sorted(datetime.strptime(f"{p.parent.name} {p.name[:6]}", "%Y-%m-%d %H%M%S").replace(tzinfo=timezone.utc)
                  for p in root.glob("*/*Z.json.gz"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=RAW)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    network, params = load_network(), load_params()
    snapshots = load_snapshots(args.raw / "status")
    segments = covered_segments(snapshot_times(args.raw / "bikepoint"))
    if not snapshots or not segments:
        print(f"no logs under {args.raw}", file=sys.stderr)
        return 1
    covered = sum((e - s).total_seconds() for s, e in segments) / 3600
    hours_open = open_hours(segments)
    print(f"covered: {covered:.1f} h in {len(segments)} stretches, {hours_open:.1f} h of them 06:00–24:00 London")

    def count(name: str, km: float) -> tuple[int, float, int]:
        st = station_by_name(network, name)
        settings = Settings(idle_drivers=IDLE, area_lat=st.lat, area_lon=st.lon, area_label=name, radius_km=km,
                            lookahead_at=None)
        return rate(alerts_in(snapshots, segments, network, params, settings), hours_open)

    london, _, london_info = count(*WHOLE_NETWORK)
    print(f"whole network ({WHOLE_NETWORK[1]:g} km): {london} alerts, {london_info} line-wide notices")
    areas = []
    for name, km in AREAS:
        n, per_week, info = count(name, km)
        areas.append(AreaRate(name, km, n, round(per_week, 1), info))
        print(f"{name} {km:g} km: {n} alerts ({per_week:.1f}/week), {info} line-wide notices")

    week = WeekCount(datetime.now(LONDON).date().isoformat(), segments[0][0].isoformat(), segments[-1][1].isoformat(),
                     round(covered, 1), round(hours_open, 1), london, areas)
    print(f"typical area: {week.typical_per_week:.1f} alerts/week")
    if not args.dry_run:
        args.out.write_text(json.dumps(week.to_dict(), indent=1) + "\n")
        print(f"wrote {args.out.relative_to(REPO) if args.out.is_relative_to(REPO) else args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

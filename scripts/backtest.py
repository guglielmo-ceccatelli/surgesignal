"""The backtest (T11): SurgeSignal's stations vs two common-sense rules, measured on Santander
bike docks. Writes surgesignal/data/backtest.json for the Evidence tab.

    python -m scripts.backtest              # all local logs
    python -m scripts.backtest --dry-run    # print only

Bike snapshots are flattened once into data/bikes.csv.gz (time × dock → bikes) and reused.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scripts.evidence_status import snapshot_times
from surgesignal.alerts.format import LONDON
from surgesignal.alerts.settings import Settings
from surgesignal.backtest import score_events, summarise
from surgesignal.console import station_by_name
from surgesignal.engine.params import load_params
from surgesignal.engine.types import ServiceArea
from surgesignal.evidence import alerts_in, covered_segments, is_actionable
from surgesignal.inputs.baseline import Baseline
from surgesignal.replay import load_snapshots
from surgesignal.tfl.network import load_network

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw"
CACHE = REPO / "data" / "bikes.csv.gz"
DOCKS = REPO / "data" / "docks.json"
OUT = REPO / "surgesignal" / "data" / "backtest.json"
# The dispatcher sits where the bikes are: Santander docks cover roughly 7 km around Charing Cross,
# so a disruption at Epping or Uxbridge has nothing to measure.
BIKE_AREA = ("Charing Cross", 7.0)


def _prop(point: dict, key: str) -> str | None:
    return next((p["value"] for p in point.get("additionalProperties", []) if p["key"] == key), None)


def load_bikes(root: Path) -> tuple[pd.DataFrame, dict[str, tuple[float, float]]]:
    """time × dock id → bikes docked, from raw BikePoint snapshots (cached; only new files are read)."""
    files = sorted(root.glob("*/*Z.json.gz"))
    frame = pd.read_csv(CACHE, index_col=0, parse_dates=True) if CACHE.exists() else pd.DataFrame()
    docks = {k: tuple(v) for k, v in json.loads(DOCKS.read_text()).items()} if DOCKS.exists() else {}
    seen = set(frame.index) if len(frame) else set()
    rows = {}
    for f in files:
        t = pd.Timestamp(datetime.strptime(f"{f.parent.name} {f.name[:6]}", "%Y-%m-%d %H%M%S").replace(tzinfo=timezone.utc))
        if t in seen:
            continue
        try:
            points = json.loads(gzip.decompress(f.read_bytes()))
        except (OSError, ValueError):
            continue
        row = {}
        for p in points:
            n = _prop(p, "NbBikes")
            if n is not None and n.isdigit():
                row[p["id"]] = int(n)
                docks.setdefault(p["id"], (p["lat"], p["lon"]))
        rows[t] = row
    if rows:
        frame = pd.concat([frame, pd.DataFrame.from_dict(rows, orient="index")]).sort_index()
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame.to_csv(CACHE)
        DOCKS.write_text(json.dumps(docks))
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame, docks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=RAW)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    network, params, baseline = load_network(), load_params(), Baseline.load()
    bikes, docks = load_bikes(args.raw / "bikepoint")
    print(f"bikes: {len(bikes)} snapshots × {len(docks)} docks")
    snapshots = load_snapshots(args.raw / "status")
    segments = covered_segments(snapshot_times(args.raw / "bikepoint"))
    name, km = BIKE_AREA
    st = station_by_name(network, name)
    settings = Settings(idle_drivers=6, area_lat=st.lat, area_lon=st.lon, area_label=name, radius_km=km, lookahead_at=None)
    events = [e for e in alerts_in(snapshots, segments, network, params, settings) if is_actionable(e)]
    print(f"alert-worthy disruptions: {len(events)}")

    scored = score_events(events, network, ServiceArea(st.lat, st.lon, km), bikes, docks, baseline.at)
    summary = summarise(scored)
    for s in scored:
        fmt = lambda v: "   –" if v is None else f"{v:+5.1f}"  # noqa: E731
        print(f"{s.label:55} engine {fmt(s.engine)}  closed {fmt(s.closed)}  busiest {fmt(s.busiest)}")
    print(f"measured on all three rules: {summary['measured']} of {summary['events']}")
    for r in summary["rules"]:
        print(f"  {r['rule']:8} mean {r['mean']:+.2f} bike moves/station/30 min (90% {r['low']:+.2f} to {r['high']:+.2f}), n={r['n']}")
    print(f"  engine beat 'closed' in {summary['engine_beats']['closed']}, 'busiest' in {summary['engine_beats']['busiest']}")

    summary["as_of"] = datetime.now(LONDON).date().isoformat()
    summary["target"] = 20
    summary["area"] = f"{km:g} km around {name}"
    if not args.dry_run:
        args.out.write_text(json.dumps(summary, indent=1) + "\n")
        print(f"wrote {args.out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

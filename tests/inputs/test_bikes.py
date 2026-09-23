import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from surgesignal.engine.types import Station
from surgesignal.inputs.bikes import from_csv, summarise, to_csv
from workers.bike_summary import write_summary

STOCKWELL = Station("940GZZLUSKW", "Stockwell", 51.472184, -0.122644)
MORDEN = Station("940GZZLUMDN", "Morden", 51.402142, -0.194839)


def dock(lat, lon, bikes, empty, total, installed="true", locked="false"):
    props = {"Installed": installed, "Locked": locked, "NbBikes": str(bikes), "NbEmptyDocks": str(empty), "NbDocks": str(total)}
    return {"lat": lat, "lon": lon, "additionalProperties": [{"key": k, "value": v} for k, v in props.items()]}


def test_sums_working_docks_near_each_station():
    points = [dock(51.4725, -0.1230, 5, 10, 15), dock(51.4740, -0.1200, 3, 2, 5),   # both < 500 m from Stockwell
              dock(51.4722, -0.1226, 9, 0, 9, locked="true"),                          # locked: ignored
              dock(51.4722, -0.1226, 9, 0, 9, installed="false"),                      # not installed: ignored
              dock(51.5000, -0.1000, 7, 7, 14)]                                        # far from both
    (row,) = summarise(points, {s.id: s for s in (STOCKWELL, MORDEN)})
    assert (row.station_id, row.bikes, row.empty_docks, row.docks, row.points) == ("940GZZLUSKW", 8, 12, 20, 2)


def test_malformed_dock_is_skipped():
    bad = {"lat": 51.4725, "lon": -0.1230, "additionalProperties": [{"key": "Installed", "value": "true"}]}
    assert summarise([bad, dock(51.4725, -0.1230, 1, 1, 2)], {STOCKWELL.id: STOCKWELL})[0].bikes == 1


def test_csv_round_trip():
    rows = summarise([dock(51.4725, -0.1230, 5, 10, 15)], {STOCKWELL.id: STOCKWELL})
    assert from_csv(to_csv(rows)) == rows


def test_real_bikepoint_snapshot_is_small_and_covers_central_london(tmp_path):
    raw = sorted(Path("data/raw/bikepoint").glob("*/*.json.gz"))
    if not raw:  # CI has no local logger data
        return
    payload = gzip.decompress(raw[-1].read_bytes())
    path = write_summary(payload, tmp_path, datetime(2026, 9, 23, 17, 5, tzinfo=timezone.utc))
    assert path.name == "170500Z.csv.gz" and path.stat().st_size < 10_000
    rows = from_csv(gzip.decompress(path.read_bytes()).decode())
    assert len(rows) > 100 and any(r.station_id == "940GZZLUOXC" for r in rows)  # Oxford Circus has docks nearby

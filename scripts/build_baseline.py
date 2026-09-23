"""Build surgesignal/data/baseline.json: how busy each station normally is, by day and 15-min band.

    busyness(station, t) = size(station, day type) × shape(station, day, 15-min band)

    size   TfL annual counts (AC2023, typical daily entries + exits, per day type), relative to the
           median station. Rows are matched to stations by name; a combined row ("Bank and Monument")
           applies to each station it names. Stations with no count borrow one from a same-named
           station within 1 km (interchanges counted under the Tube), else get the median.
    shape  TfL /crowding/{station} (percentage of baseline per 15-min band), divided by the day's
           mean so it averages 1. A profile that doesn't resemble London's typical shape for that
           day (correlation < MIN_CORR) is replaced by the typical shape: some TfL profiles are
           implausible (Clapham Common's Friday peaks at midnight). Plausible ones are blended
           SHAPE_BLEND : (1 - SHAPE_BLEND) with the typical shape (shrinkage), because even those
           can carry odd dips (Waterloo, Friday 18:00).

    python -m scripts.build_baseline --fetch        # download counts + crowding (~3 min), then build
    python -m scripts.build_baseline                # rebuild from data/tfl/ (git-ignored raw files)

Every station records where its numbers came from, for the "how this works" panel.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any

from surgesignal.inputs.baseline import DAYS, DEFAULT_PATH, day_type
from surgesignal.tfl.names import lookup_keys, normalise
from surgesignal.tfl.network import SAME_PLACE_KM, Network, load_network
from workers.logger import http_fetch

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "tfl"
COUNTS_URL = "https://crowding.data.tfl.gov.uk/Annual%20Station%20Counts/2023/AC2023_AnnualisedEntryExit.xlsx"
COUNTS_FILE = RAW / "AC2023_AnnualisedEntryExit.xlsx"
CROWDING_DIR = RAW / "crowding"
MIN_CORR = 0.6
# Station profiles are noisy (Waterloo's Friday dips to 0.6× at 18:00, mid-peak): each plausible
# profile is shrunk halfway towards London's typical shape, so one odd band can't dominate.
SHAPE_BLEND = 0.5
BANDS = 96
MODE_PREFIX = {"LU": "940G", "EZL": "910G"}
SUFFIXES = (" LU", " EZL", " NR", " TfL", " DLR", " LO")
# Rows whose names don't match a station by normalising alone: (mode, row name) -> stationIds
EXPLICIT = {
    ("LU", "Bank and Monument"): ("940GZZLUBNK", "940GZZLUMMT"),
    ("LU", "Heathrow Terminals 123 LU"): ("940GZZLUHRC",),
    ("LU", "Edgware Road (Bak)"): ("940GZZLUERB",),
    ("LU", "Edgware Road (DIS)"): ("940GZZLUERC",),
    ("LU", "Hammersmith (DIS)"): ("940GZZLUHSD",),
    ("LU", "Hammersmith (H&C)"): ("940GZZLUHSC",),
    ("LU", "Paddington TfL"): ("940GZZLUPAC", "940GZZLUPAH"),
    ("LU", "Shepherd's Bush LU"): ("940GZZLUSBC",),
}
# Column offsets in the AC2023 sheet: entries Mon, Tue-Thu, Fri, Sat, Sun then exits in the same order.
DAYTYPE_COLS = {"MON": (6, 11), "MIDWEEK": (7, 12), "FRI": (8, 13), "SAT": (9, 14), "SUN": (10, 15)}


# ---- size -----------------------------------------------------------------------------


def read_counts(path: Path) -> list[tuple[str, str, dict[str, float] | None]]:
    """(mode, station name, {day type: entries+exits}) per row; None where TfL shows '---'."""
    import openpyxl  # build-time only (requirements-dev.txt)

    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    rows = []
    for r in ws.iter_rows(min_row=8, values_only=True):
        if not r[0] or not r[3]:
            continue
        values: dict[str, float] | None = {}
        for dt, (e, x) in DAYTYPE_COLS.items():
            if not isinstance(r[e], (int, float)) or not isinstance(r[x], (int, float)):
                values = None
                break
            values[dt] = float(r[e] + r[x])
        rows.append((str(r[0]), str(r[3]), values))
    return rows


def match_row(mode: str, name: str, network: Network) -> tuple[str, ...]:
    if (mode, name) in EXPLICIT:
        return EXPLICIT[(mode, name)]
    prefix = MODE_PREFIX.get(mode)
    if prefix is None:
        return ()
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    key = normalise(name)
    return tuple(sorted(s.id for s in network.stations.values() if s.id.startswith(prefix) and key in lookup_keys(s.name)))


def build_sizes(rows: list[tuple[str, str, dict[str, float] | None]], network: Network) -> tuple[dict[str, dict[str, float]], dict[str, str], list[str]]:
    """Relative size per station per day type, where each came from, and unmatched rows."""
    raw: dict[str, dict[str, float]] = {}
    unmatched = []
    for mode, name, values in rows:
        if mode not in MODE_PREFIX:
            continue
        ids = match_row(mode, name, network)
        if not ids:
            unmatched.append(f"{mode} {name}")
            continue
        if values is not None:
            for sid in ids:
                raw[sid] = values
    median = statistics.median(v["MIDWEEK"] for v in raw.values())
    sizes: dict[str, dict[str, float]] = {}
    source: dict[str, str] = {}
    for sid, st in network.stations.items():
        if sid in raw:
            sizes[sid], source[sid] = raw[sid], "counts"
            continue
        near = [raw[o] for o in raw if network.stations[o].name == st.name and network._same_place({sid, o})]
        if near:
            sizes[sid] = {dt: max(v[dt] for v in near) for dt in DAYTYPE_COLS}
            source[sid] = "same-place"
        else:
            sizes[sid] = {dt: median for dt in DAYTYPE_COLS}
            source[sid] = "median"
    relative = {sid: {dt: round(v / median, 3) for dt, v in dts.items()} for sid, dts in sizes.items()}
    return relative, source, unmatched


# ---- shape ----------------------------------------------------------------------------


def normalise_profile(values: list[float]) -> list[float] | None:
    if len(values) != BANDS:
        return None
    mean = sum(values) / BANDS
    return [v / mean for v in values] if mean > 0 else None


def correlation(a: list[float], b: list[float]) -> float:
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return cov / (va * vb) ** 0.5 if va > 0 and vb > 0 else 0.0


def parse_crowding(data: dict[str, Any]) -> dict[str, list[float]]:
    """{day: normalised 96-band profile} from a /crowding/{station} response."""
    out = {}
    for day in data.get("daysOfWeek", []):
        values = [b["percentageOfBaseLine"] for b in day.get("timeBands", [])]
        prof = normalise_profile(values)
        if prof is not None and day.get("dayOfWeek") in DAYS:
            out[day["dayOfWeek"]] = prof
    return out


def build_shapes(profiles: dict[str, dict[str, list[float]]]) -> tuple[dict[str, list[float]], dict[str, dict[str, list[float] | None]], dict[str, int]]:
    """Network median shape per day, per-station shapes (None = use network), fallback counts."""
    network_shape = {}
    for day in DAYS:
        day_profiles = [p[day] for p in profiles.values() if day in p]
        network_shape[day] = [statistics.median(p[i] for p in day_profiles) for i in range(BANDS)]
    shapes: dict[str, dict[str, list[float] | None]] = {}
    fallbacks = {"implausible": 0, "missing": 0}
    for sid, days in profiles.items():
        shapes[sid] = {}
        for day in DAYS:
            prof = days.get(day)
            if prof is None:
                shapes[sid][day] = None
                fallbacks["missing"] += 1
            elif correlation(prof, network_shape[day]) < MIN_CORR:
                shapes[sid][day] = None
                fallbacks["implausible"] += 1
            else:
                typical = network_shape[day]
                shapes[sid][day] = [SHAPE_BLEND * p + (1 - SHAPE_BLEND) * n for p, n in zip(prof, typical)]
    return network_shape, shapes, fallbacks


def peak_windows(shape: list[float], start_band: int, end_band: int, width: int = 8) -> tuple[int, int]:
    """Busiest `width`-band (2 h) window starting in [start_band, end_band)."""
    best = max(range(start_band, end_band - width + 1), key=lambda i: sum(shape[i : i + width]))
    return best, best + width


# ---- fetch + assemble -----------------------------------------------------------------


def fetch_all(network: Network) -> None:
    CROWDING_DIR.mkdir(parents=True, exist_ok=True)
    if not COUNTS_FILE.exists():
        COUNTS_FILE.write_bytes(http_fetch(COUNTS_URL))
    for i, sid in enumerate(sorted(network.stations), 1):
        path = CROWDING_DIR / f"{sid}.json"
        if path.exists():
            continue
        for attempt in range(4):
            try:
                path.write_bytes(http_fetch(f"https://api.tfl.gov.uk/crowding/{sid}"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 3:  # rate limited: back off and retry
                    time.sleep(20 * (attempt + 1))
                    continue
                # 404 for stations without crowding data: they use the network shape
                print(f"  {sid} {network.stations[sid].name}: {exc}", file=sys.stderr)
                break
        time.sleep(1.2)  # stay inside TfL's anonymous rate limit
        if i % 50 == 0:
            print(f"  crowding {i}/{len(network.stations)}")


def build(network: Network) -> dict[str, Any]:
    sizes, size_source, unmatched = build_sizes(read_counts(COUNTS_FILE), network)
    profiles = {}
    for path in CROWDING_DIR.glob("*.json"):
        try:
            profiles[path.stem] = parse_crowding(json.loads(path.read_text()))
        except ValueError:
            continue
    network_shape, shapes, fallbacks = build_shapes({k: v for k, v in profiles.items() if k in network.stations})
    peaks = {}
    for day in DAYS:
        if day_type(day) in ("SAT", "SUN"):
            continue
        am = peak_windows(network_shape[day], 5 * 4, 12 * 4)
        pm = peak_windows(network_shape[day], 14 * 4, 21 * 4)
        peaks[day] = {"am": am, "pm": pm}
    r = lambda xs: [round(x, 2) for x in xs]  # noqa: E731
    return {
        "source": {
            "size": "TfL annual station counts 2023 (AC2023), typical daily entries + exits, relative to the median station",
            "shape": f"TfL /crowding API, 15-min bands normalised to the day's mean, blended {SHAPE_BLEND:g}/{1 - SHAPE_BLEND:g} with the network median shape; network median alone where a station's profile correlates < {MIN_CORR} with it or is missing",
            "built": time.strftime("%Y-%m-%d"),
        },
        "network_shape": {d: r(s) for d, s in network_shape.items()},
        "peaks": peaks,
        "stations": {
            sid: {
                "size": sizes[sid],
                "size_source": size_source[sid],
                "shape": {d: (r(s) if s is not None else None) for d, s in shapes.get(sid, {}).items()},
            }
            for sid in sorted(network.stations)
        },
        "report": {"unmatched_count_rows": unmatched, "shape_fallbacks": fallbacks,
                   "size_sources": {k: sum(1 for v in size_source.values() if v == k) for k in ("counts", "same-place", "median")}},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args(argv)
    network = load_network()
    if args.fetch:
        fetch_all(network)
    if not COUNTS_FILE.exists():
        print(f"missing {COUNTS_FILE} (use --fetch)", file=sys.stderr)
        return 1
    data = build(network)
    args.out.write_text(json.dumps(data, separators=(",", ":")) + "\n")
    print(json.dumps(data["report"], indent=1))
    print(f"-> {args.out} ({args.out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

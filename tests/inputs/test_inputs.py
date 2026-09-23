import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.build_baseline import (
    SHAPE_BLEND,
    build_shapes,
    build_sizes,
    correlation,
    match_row,
    normalise_profile,
    parse_crowding,
    peak_windows,
)
from surgesignal.inputs.baseline import Baseline, day_type
from surgesignal.inputs.events import load_events, load_venues
from surgesignal.inputs.weather import CACHE_FOR, WeatherSource, open_meteo_url, parse

FRI_1807 = datetime(2026, 9, 25, 17, 7, tzinfo=timezone.utc)  # Friday 18:07 London (BST)


# ---- baseline: committed data ---------------------------------------------------------


@pytest.fixture(scope="module")
def baseline():
    return Baseline.load()


def test_busy_stations_outrank_quiet_ones(baseline):
    at = baseline.at(FRI_1807)
    assert at["940GZZLUOXC"] > 4 * at["940GZZLUSKW"] > 0  # Oxford Circus vs Stockwell
    assert at["940GZZLUICK"] < 1  # Ickenham: quiet


def test_every_station_has_a_positive_value(baseline, net):
    at = baseline.at(FRI_1807)
    assert set(at) == set(net.stations) and min(at.values()) > 0


def test_same_time_differs_by_day_and_hour(baseline):
    wed_0830 = datetime(2026, 9, 23, 7, 30, tzinfo=timezone.utc)
    sun_0830 = datetime(2026, 9, 27, 7, 30, tzinfo=timezone.utc)
    assert baseline.at(wed_0830)["940GZZLUOXC"] > 3 * baseline.at(sun_0830)["940GZZLUOXC"]


def test_london_time_is_used_across_the_clock_change(baseline):
    # 17:30 UTC is 18:30 London in September (BST) but 17:30 London in November (GMT)
    assert baseline.time_label(datetime(2026, 9, 25, 17, 30, tzinfo=timezone.utc)) == "Friday evening peak"
    assert baseline.time_label(datetime(2026, 11, 6, 17, 30, tzinfo=timezone.utc)) == "Friday evening peak"
    assert baseline.time_label(datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)) is None
    assert baseline.time_label(datetime(2026, 9, 26, 17, 30, tzinfo=timezone.utc)) is None  # Saturday


def test_implausible_profile_uses_network_shape(baseline):
    assert baseline.sources("940GZZLUCPC")["shape"]["FRI"] == "network"  # Clapham Common's Friday
    assert baseline.sources("940GZZLUOXC")["shape"]["FRI"] == "station"


def test_interchange_borrows_size(baseline):
    assert baseline.sources("910GPADTLL")["size"] == "same-place"


def test_naive_time_rejected(baseline):
    with pytest.raises(ValueError):
        baseline.at(datetime(2026, 9, 25, 18, 7))


def test_day_types():
    assert [day_type(d) for d in ("MON", "TUE", "THU", "FRI", "SUN")] == ["MON", "MIDWEEK", "MIDWEEK", "FRI", "SUN"]


# ---- baseline: build rules ------------------------------------------------------------


def test_match_row_strips_mode_suffix_and_filters_mode(net):
    assert match_row("LU", "Balham LU", net) == ("940GZZLUBLM",)
    assert match_row("EZL", "Woolwich EZL", net) == ("910GWOLWXR",)
    assert match_row("LU", "Bank and Monument", net) == ("940GZZLUBNK", "940GZZLUMMT")
    assert match_row("DLR", "Bank", net) == ()


def test_build_sizes_rules(net):
    v = {"MON": 10.0, "MIDWEEK": 10.0, "FRI": 10.0, "SAT": 5.0, "SUN": 5.0}
    rows = [("LU", "Stockwell", v), ("LU", "Paddington TfL", {k: x * 4 for k, x in v.items()}),
            ("EZL", "Paddington TfL", None), ("LU", "Nowhere LU", v)]
    sizes, source, unmatched = build_sizes(rows, net)
    assert unmatched == ["LU Nowhere LU"]
    assert source["940GZZLUSKW"] == "counts" and source["910GPADTLL"] == "same-place"
    assert sizes["910GPADTLL"] == sizes["940GZZLUPAC"]  # borrowed from the Tube station at the same place
    assert source["940GZZLUMDN"] == "median"


def test_normalise_and_correlation():
    assert normalise_profile([1.0] * 96) == [1.0] * 96
    assert normalise_profile([0.0] * 96) is None and normalise_profile([1.0] * 95) is None
    a = [float(i % 10) for i in range(96)]
    assert correlation(a, a) == pytest.approx(1.0) and correlation(a, [-x for x in a]) == pytest.approx(-1.0)


def test_build_shapes_blends_and_rejects():
    peak = [1.0] * 96
    for i in range(68, 76):
        peak[i] = 3.0
    peak = normalise_profile(peak)
    backwards = normalise_profile([3.0 if i < 8 else 1.0 for i in range(96)])  # peaks at midnight
    days = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
    profiles = {"a": {d: peak for d in days}, "b": {d: peak for d in days}, "c": {"FRI": backwards}}
    network, shapes, fallbacks = build_shapes(profiles)
    assert shapes["a"]["FRI"] == pytest.approx([SHAPE_BLEND * p + (1 - SHAPE_BLEND) * n for p, n in zip(peak, network["FRI"])])
    assert shapes["c"]["FRI"] is None  # implausible → network shape
    assert fallbacks == {"implausible": 1, "missing": 6}


def test_parse_crowding_skips_all_zero_days():
    data = {"daysOfWeek": [{"dayOfWeek": "FRI", "timeBands": [{"percentageOfBaseLine": 0.0}] * 96},
                           {"dayOfWeek": "SAT", "timeBands": [{"percentageOfBaseLine": 0.2}] * 96}]}
    assert set(parse_crowding(data)) == {"SAT"}


def test_peak_window_finds_busiest_two_hours():
    shape = [1.0] * 96
    for i in range(70, 78):
        shape[i] = 2.0
    assert peak_windows(shape, 56, 84) == (70, 78)


# ---- weather --------------------------------------------------------------------------


def om(precip, temp):
    return json.dumps({"current": {"precipitation": precip, "temperature_2m": temp}}).encode()


def test_weather_parse_and_url():
    assert parse(om(2.4, 11.5)).precip_mm_h == 2.4
    url = open_meteo_url(51.46174, -0.13832)
    assert "latitude=51.4617" in url and "current=precipitation%2Ctemperature_2m" in url


def test_weather_is_cached_then_refreshed():
    calls = []
    src = WeatherSource(fetch=lambda url: calls.append(url) or om(1.0, 10.0))
    src.current(51.46, -0.14, FRI_1807)
    src.current(51.46, -0.14, FRI_1807 + timedelta(minutes=5))
    src.current(51.46, -0.14, FRI_1807 + CACHE_FOR + timedelta(seconds=1))
    assert len(calls) == 2


def test_weather_failure_returns_none_and_remembers_why():
    def blocked(url):
        raise OSError("SSL certificate problem")
    src = WeatherSource(fetch=blocked)
    assert src.current(51.46, -0.14, FRI_1807) is None and "SSL" in src.last_error


# ---- events ---------------------------------------------------------------------------


def test_venues_load():
    venues = load_venues()
    assert venues["Wembley Stadium"].capacity == 90000 and len(venues) >= 6


def test_events_file(tmp_path):
    path = tmp_path / "events.csv"
    path.write_text("# comment\nvenue,name,ends_at\n"
                    "Wembley Stadium,England v Brazil,2026-10-14 21:45\n"
                    "Narnia Arena,Lion Show,2026-10-14 21:45\n"
                    "The O2,Late gig,14/10/2026\n")
    events, problems = load_events(load_venues(), path)
    (e,) = events
    assert e.name == "England v Brazil" and e.capacity == 90000
    assert e.ends_at == datetime(2026, 10, 14, 20, 45, tzinfo=timezone.utc)  # 21:45 BST
    assert len(problems) == 2 and "Narnia Arena" in problems[0] and "ends_at" in problems[1]


def test_missing_events_file_means_no_events(tmp_path):
    assert load_events(load_venues(), tmp_path / "nope.csv") == ([], [])


def test_committed_events_file_is_valid():
    events, problems = load_events(load_venues())
    assert problems == []

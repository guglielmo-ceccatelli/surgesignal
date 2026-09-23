import math
from datetime import datetime, timedelta

import pytest

from surgesignal.engine import model
from surgesignal.engine.geo import distance_to_section
from surgesignal.engine.types import Conditions, Event, ServiceArea
from tests.engine.conftest import KENNINGTON, MORDEN_BRANCH, NOW, OVAL, make_disruption

DRY = Conditions()


def run(params, stations, disruptions, area, conditions=DRY, baseline=None, now=NOW):
    baseline = baseline if baseline is not None else {sid: 1.0 for sid in stations}
    return model.run(stations, disruptions, conditions, baseline, area, now, params)


def by_id(result):
    return {h.station.id: h for h in result.hotspots}


def test_unplanned_suspension_in_dry_weather_is_about_plus_33_percent(params, northern_stations, wide_area):
    h = by_id(run(params, northern_stations, [make_disruption()], wide_area))["940GZZLUSKW"]
    assert h.pct.mid == pytest.approx(0.325)


def test_uplift_decays_with_distance_outside_the_section(params, northern_stations, wide_area):
    d = make_disruption()
    oval = northern_stations[OVAL]
    dist = distance_to_section(oval, d.affected_station_ids, northern_stations)
    h = by_id(run(params, northern_stations, [d], wide_area))[OVAL]
    assert h.uplift.mid == pytest.approx(0.325 * math.exp(-dist / params.lambda_km))
    assert by_id(run(params, northern_stations, [d], wide_area))[KENNINGTON].uplift.mid < h.uplift.mid


def test_range_uses_both_severity_and_lambda_factors(params, northern_stations, wide_area):
    d = make_disruption()
    oval = northern_stations[OVAL]
    dist = distance_to_section(oval, d.affected_station_ids, northern_stations)
    h = by_id(run(params, northern_stations, [d], wide_area))[OVAL]
    assert h.uplift.low == pytest.approx(0.325 * 0.5 * math.exp(-dist / (0.8 * 0.5)))
    assert h.uplift.high == pytest.approx(0.325 * 1.5 * math.exp(-dist / (0.8 * 1.5)))
    assert h.uplift.low < h.uplift.mid < h.uplift.high


def test_planned_disruption_is_weaker_than_unplanned(params, northern_stations, wide_area):
    planned = by_id(run(params, northern_stations, [make_disruption(is_unplanned=False)], wide_area))
    assert planned["940GZZLUSKW"].uplift.mid == pytest.approx(0.25 * 0.6)


def test_area_uncertain_fallback_halves_severity_and_is_reported(params, northern_stations, wide_area):
    d = make_disruption(area_uncertain=True, severity_scale=0.5)
    result = run(params, northern_stations, [d], wide_area)
    assert by_id(result)["940GZZLUSKW"].uplift.mid == pytest.approx(0.1625)
    assert result.area_uncertain == (d,)


def test_line_wide_disruption_is_never_ranked(params, northern_stations, wide_area):
    d = make_disruption(line_wide=True, affected_station_ids=())
    result = run(params, northern_stations, [d], wide_area)
    assert result.hotspots == ()
    assert result.line_wide == (d,)


def test_overlapping_disruptions_add_up(params, northern_stations, wide_area):
    a = make_disruption()
    b = make_disruption(key="second", severity_class="severe")
    h = by_id(run(params, northern_stations, [a, b], wide_area))["940GZZLUSKW"]
    assert h.uplift.mid == pytest.approx(0.325 + 0.12 * 1.3)
    assert h.disruption_keys == (a.key, b.key)


def test_rain_alone_raises_pct_but_not_disruption_uplift(params, northern_stations, wide_area):
    h = by_id(run(params, northern_stations, [], wide_area, Conditions(precip_mm_h=3.0)))["940GZZLUSKW"]
    assert h.uplift.mid == 0
    assert h.pct.mid == pytest.approx(0.25)
    assert h.uplift.mid < params.alert_threshold_uplift


@pytest.mark.parametrize("precip,temp,expected", [
    (0.0, 15, 1.0), (0.05, 15, 1.0), (0.1, 15, 1.1), (1.99, 15, 1.1), (2.0, 15, 1.25), (0.0, 2.9, 1.05), (5.0, -1, 1.30),
])
def test_weather_multiplier_bands(params, precip, temp, expected):
    assert model.weather_mult(Conditions(precip_mm_h=precip, temp_c=temp), params) == pytest.approx(expected)


def event(station, capacity, ends_in_min):
    return Event("match", station.lat, station.lon, capacity, NOW + timedelta(minutes=ends_in_min))


@pytest.mark.parametrize("capacity,ends_in,expected", [
    (9_999, 30, 1.0), (10_000, 30, 1.15), (40_000, 30, 1.3), (60_000, 61, 1.0), (60_000, -1, 1.0),
])
def test_event_multiplier_tiers_and_window(params, northern_stations, capacity, ends_in, expected):
    s = northern_stations["940GZZLUSKW"]
    c = Conditions(events=(event(s, capacity, ends_in),))
    assert model.event_mult(s, c, NOW, params) == pytest.approx(expected)


def test_events_take_the_largest_multiplier_not_the_product(params, northern_stations):
    s = northern_stations["940GZZLUSKW"]
    c = Conditions(events=(event(s, 12_000, 10), event(s, 50_000, 20)))
    assert model.event_mult(s, c, NOW, params) == pytest.approx(1.3)


def test_event_outside_radius_is_ignored(params, northern_stations):
    far = northern_stations["940GZZLUMDN"]
    c = Conditions(events=(event(far, 50_000, 10),))
    assert model.event_mult(northern_stations["940GZZLUSKW"], c, NOW, params) == 1.0


def test_stations_outside_service_area_are_excluded(params, northern_stations):
    s = northern_stations["940GZZLUSKW"]
    tiny = ServiceArea(s.lat, s.lon, radius_km=0.5)
    assert [h.station.id for h in run(params, northern_stations, [make_disruption()], tiny).hotspots] == ["940GZZLUSKW"]


def test_station_missing_from_baseline_scores_but_ranks_last(params, northern_stations, wide_area):
    baseline = {sid: 1.0 for sid in MORDEN_BRANCH if sid != "940GZZLUSKW"}
    result = run(params, northern_stations, [make_disruption()], wide_area, baseline=baseline)
    stockwell = by_id(result)["940GZZLUSKW"]
    assert stockwell.extra == 0 and stockwell.pct.mid > 0
    assert result.hotspots[0].station.id != "940GZZLUSKW"


def test_before_t0_there_is_no_effect(params, northern_stations, wide_area):
    result = run(params, northern_stations, [make_disruption()], wide_area, now=NOW - timedelta(hours=1))
    assert result.hotspots == ()


def test_naive_now_is_rejected(params, northern_stations, wide_area):
    with pytest.raises(ValueError, match="timezone-aware"):
        run(params, northern_stations, [], wide_area, now=datetime(2026, 9, 25, 18, 0))


def test_unknown_affected_station_id_fails_loudly(params, northern_stations, wide_area):
    d = make_disruption(affected_station_ids=("940GZZLUXXX",))
    with pytest.raises(KeyError):
        run(params, northern_stations, [d], wide_area)

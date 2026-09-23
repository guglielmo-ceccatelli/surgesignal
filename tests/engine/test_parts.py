"""Unit tests for params loading, time profile, geo, sizing, explanation and type validation."""

from datetime import datetime, timedelta, timezone

import pytest
import yaml

from surgesignal.engine import sizing
from surgesignal.engine.explain import explain
from surgesignal.engine.geo import haversine_km, in_service_area
from surgesignal.engine.params import DEFAULT_PATH, ParamsError, load_params, params_markdown_table
from surgesignal.engine.timeprofile import expected_duration_min, g
from surgesignal.engine.types import Conditions, Hotspot, Range, ServiceArea, Station
from tests.engine.conftest import T0, make_disruption

# ---- params ---------------------------------------------------------------------------


def write_params(tmp_path, mutate):
    data = yaml.safe_load(DEFAULT_PATH.read_text())
    mutate(data)
    path = tmp_path / "params.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_real_params_file_loads(params):
    assert params.severity("suspended") == 0.25 and params.top_n == 3 and isinstance(params.top_n, int)


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d.pop("lambda_km"), "missing=\\['lambda_km'\\]"),
    (lambda d: d.update(lamda_km={"value": 1, "unit": "km", "source": "x"}), "unknown=\\['lamda_km'\\]"),
    (lambda d: d["lambda_km"].pop("source"), "needs value, unit, source"),
    (lambda d: d["lambda_km"].update(value="0.8"), "must be a number"),
    (lambda d: d["lambda_km"].update(value=True), "must be a number"),
    (lambda d: d["lambda_km"].update(value=-1), "must not be negative"),
])
def test_bad_params_fail_fast(tmp_path, mutate, match):
    with pytest.raises(ParamsError, match=match):
        load_params(write_params(tmp_path, mutate))


def test_markdown_table_has_a_row_per_parameter():
    table = params_markdown_table()
    n = len(yaml.safe_load(DEFAULT_PATH.read_text()))
    assert len(table.splitlines()) == n + 2
    assert "| `severity_suspended` | 0.25 | fraction of baseline | assumption |" in table


# ---- time profile ---------------------------------------------------------------------


@pytest.mark.parametrize("minutes,expected", [(-1, 0.0), (0, 0.0), (4, 0.5), (8, 1.0), (60, 1.0)])
def test_g_ramps_then_plateaus_while_active(params, minutes, expected):
    assert g(make_disruption(), T0 + timedelta(minutes=minutes), params) == pytest.approx(expected)


def test_g_decays_by_half_each_half_life_after_resolution(params):
    d = make_disruption(resolved_at=T0 + timedelta(minutes=30))
    assert g(d, T0 + timedelta(minutes=30), params) == pytest.approx(1.0)
    assert g(d, T0 + timedelta(minutes=45), params) == pytest.approx(0.5)
    assert g(d, T0 + timedelta(minutes=60), params) == pytest.approx(0.25)


def test_g_resolved_during_ramp_decays_from_reached_level(params):
    d = make_disruption(resolved_at=T0 + timedelta(minutes=4))
    assert g(d, T0 + timedelta(minutes=19), params) == pytest.approx(0.25)


def test_active_duration_stretches_past_the_default(params):
    d = make_disruption()  # suspended, default 90
    assert expected_duration_min(d, T0 + timedelta(minutes=10), params) == 90
    assert expected_duration_min(d, T0 + timedelta(minutes=100), params) == 145


def test_explicit_duration_overrides_default(params):
    d = make_disruption(expected_duration_min=30)
    assert expected_duration_min(d, T0, params) == 30


# ---- geo ------------------------------------------------------------------------------


def test_haversine_known_distance():
    # 1 degree of latitude ≈ 111.2 km
    assert haversine_km(51.0, 0.0, 52.0, 0.0) == pytest.approx(111.2, abs=0.1)


def test_service_area_boundary():
    s = Station("x", "X", 51.5, -0.1)
    assert in_service_area(s, ServiceArea(51.5, -0.1, 0.0))
    assert not in_service_area(s, ServiceArea(51.6, -0.1, 5.0))


# ---- sizing ---------------------------------------------------------------------------


def hs(extra, pct_mid=0.6):
    return Hotspot(Station(str(extra), "S", 0, 0), Range(0, 0, 0), Range(0, pct_mid, 0), 1.0, extra, "")


def test_round_half_up_is_not_bankers_rounding():
    assert sizing.round_half_up(2.5) == 3 and sizing.round_half_up(1.49) == 1


@pytest.mark.parametrize("idle,expected", [(None, None), (0, 0), (-3, 0), (1, 1), (6, 2), (40, 12)])
def test_cars_to_move(params, idle, expected):
    assert sizing.cars_to_move([hs(1.0, 0.6)], idle, params) == expected


def test_cars_at_least_one_when_any_demand(params):
    assert sizing.cars_to_move([hs(1.0, 0.01)], 3, params) == 1


def test_cars_never_exceed_idle(params):
    assert sizing.cars_to_move([hs(1.0, 5.0)], 4, params) == 4


def test_no_cars_without_top_stations(params):
    assert sizing.cars_to_move([], 6, params) == 0


@pytest.mark.parametrize("extras,cars,expected", [
    ([3.0, 2.0, 1.0], 2, [1, 1, 0]),
    ([3.0, 2.0, 1.0], 6, [3, 2, 1]),
    ([1.0, 1.0, 1.0], 2, [1, 1, 0]),
    ([0.0, 0.0, 0.0], 3, [1, 1, 1]),
    ([5.0, 1.0], 0, [0, 0]),
])
def test_allocation_sums_exactly(extras, cars, expected):
    counts = sizing.allocate([hs(e) for e in extras], cars)
    assert counts == expected and sum(counts) == cars


# ---- explanation ----------------------------------------------------------------------


def test_explanation_orders_by_effect_and_caps_parts(params):
    a = make_disruption(key="a")
    b = make_disruption(key="b", line_name="Victoria", severity_class="minor")
    s = Station("s", "S", 0, 0)
    text = explain(s, [a, b], {"a": 0.4, "b": 0.01}, Conditions(precip_mm_h=0.5, temp_c=1), 1.15, 1.3, params)
    assert text == "Northern line suspended (unplanned) + event ending nearby + light rain + cold"


def test_explanation_for_quiet_station(params):
    assert explain(Station("s", "Oval", 0, 0), [], {}, Conditions(), 1.0, 1.0, params) == "Normal conditions at Oval"


# ---- types ----------------------------------------------------------------------------


@pytest.mark.parametrize("kw,match", [
    (dict(severity_class="catastrophic"), "unknown severity_class"),
    (dict(t0=datetime(2026, 9, 25, 17, 7)), "timezone-aware"),
    (dict(affected_station_ids=()), "no stations"),
])
def test_disruption_validation(kw, match):
    with pytest.raises(ValueError, match=match):
        make_disruption(**kw)


def test_line_wide_disruption_may_have_no_stations():
    assert make_disruption(line_wide=True, affected_station_ids=()).line_wide

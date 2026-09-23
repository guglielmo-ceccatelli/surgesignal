"""The design doc's worked example, pinned. If this fails, the numbers the judges see changed.

Northern line suspended Stockwell–Morden (unplanned, 18:07 Fri), heavy rain, 6 idle cars:
  uplift = 0.25 × 1.3 = 0.325 (low 0.1625, high 0.4875)
  pct    = (1 + uplift) × 1.25 − 1 = +65.6% (+45.3% to +85.9%)
  cars   = round_half_up(0.5 × 0.65625 × 6) = 2
  until at least 18:37 UTC = 19:37 BST
"""

import pytest

from surgesignal.engine import model, sizing
from surgesignal.engine.timeprofile import expected_end
from surgesignal.engine.types import Conditions
from tests.engine.conftest import MORDEN_BRANCH, NOW, T0, make_disruption

BASELINE = {  # illustrative relative busyness; decides which 3 of the 10 stations rank top
    "940GZZLUSKW": 1.00, "940GZZLUCPN": 0.70, "940GZZLUCPC": 0.90, "940GZZLUCPS": 0.80,
    "940GZZLUBLM": 0.85, "940GZZLUTBC": 0.50, "940GZZLUTBY": 0.75, "940GZZLUCSD": 0.40,
    "940GZZLUSWN": 0.35, "940GZZLUMDN": 0.60,
}


@pytest.fixture
def result(params, northern_stations, wide_area):
    conditions = Conditions(precip_mm_h=3.0, temp_c=12.0, time_label="Friday peak")
    return model.run(northern_stations, [make_disruption()], conditions, BASELINE, wide_area, NOW, params)


def test_every_station_in_the_section_gets_the_worked_example_numbers(result):
    by_id = {h.station.id: h for h in result.hotspots}
    for sid in MORDEN_BRANCH:
        h = by_id[sid]
        assert h.uplift.mid == pytest.approx(0.325)
        assert h.uplift.low == pytest.approx(0.1625)
        assert h.uplift.high == pytest.approx(0.4875)
        assert h.pct.mid == pytest.approx(0.65625)
        assert h.pct.low == pytest.approx(0.453125)
        assert h.pct.high == pytest.approx(0.859375)


def test_top_three_are_ranked_by_baseline_busyness(result, params):
    top = result.hotspots[: params.top_n]
    assert [h.station.name for h in top] == ["Stockwell", "Clapham Common", "Balham"]


def test_worked_example_moves_two_of_six_cars(result, params):
    top = result.hotspots[: params.top_n]
    cars = sizing.cars_to_move(top, idle_drivers=6, p=params)
    assert cars == 2
    assert sum(sizing.allocate(top, cars)) == 2


def test_worked_example_explanation(result):
    assert result.hotspots[0].explanation == "Northern line suspended (unplanned) + heavy rain + Friday peak"


def test_worked_example_until_at_least_1937_london(params):
    end = expected_end(make_disruption(), NOW, params)
    assert (end - T0).total_seconds() / 60 == 90
    assert end.strftime("%H:%M") == "18:37"  # UTC; 19:37 BST

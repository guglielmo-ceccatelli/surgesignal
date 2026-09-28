import pytest

from surgesignal.economics import DEFAULTS, EconInputs, evaluate


def test_defaults_worked_example():
    r = evaluate(EconInputs())
    assert (r.rider_saving_low, r.rider_saving_high) == (7.5, 22.5)  # £15 × (1.5−1), £15 × (2.5−1)
    assert r.pickup_gain_min == 15
    assert r.jobs_per_week == 6 and r.weekly_revenue == 90 and r.yearly_revenue == 4680


def test_every_default_is_labelled_with_a_source():
    for key, inp in DEFAULTS.items():
        assert inp.kind in ("EVIDENCE", "ESTIMATE") and inp.source, key
    assert DEFAULTS["fare_gbp"].kind == "ESTIMATE"  # placeholder until the operator's fare


@pytest.mark.parametrize("kw", [dict(fare_gbp=-1), dict(surge_low=0.9), dict(surge_low=2.0, surge_high=1.8)])
def test_invalid_inputs(kw):
    with pytest.raises(ValueError):
        EconInputs(**kw)


def test_pickup_gain_never_negative():
    assert evaluate(EconInputs(pickup_placed_min=25, pickup_cold_min=20)).pickup_gain_min == 0


def test_alerts_per_week_come_from_the_logs_when_counted():
    from surgesignal.economics import with_evidence

    ev = {"open_hours": 27.0, "typical_per_week": 23.3,
          "areas": [{"per_week": 23.3}, {"per_week": 14.0}, {"per_week": 46.6}]}
    d = with_evidence(ev)["alerts_per_week"]
    assert d.value == 14.0 and d.kind == "EVIDENCE" and "14–46.6" in d.source
    assert with_evidence(None)["alerts_per_week"].kind == "ESTIMATE"

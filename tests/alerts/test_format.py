from surgesignal.alerts.format import format_alert, format_line_wide, top_for
from surgesignal.engine.types import Conditions
from tests.alerts.conftest import NOW, make_ctx, northern


def top(net, params, d, **kw):
    ctx = make_ctx(net, params, [d], **kw)
    return top_for(ctx.result, d, params)


def test_worked_example_text_with_rain(net, params):
    d = northern()
    text = format_alert(d, top(net, params, d, conditions=Conditions(precip_mm_h=3.0)), 6, NOW, params)
    assert text.splitlines() == [
        "⚠️ Northern line suspended (unplanned, 18:07).",
        "Expect +45–86% booking demand around Balham, Clapham Common, Clapham North until at least ~19:37.",
        "Why: Northern line suspended (unplanned) + heavy rain",
        "Suggest: move 2 of your 6 idle cars to wait nearby (not at the station): Balham 1, Clapham Common 1.",
    ]


def test_without_idle_setting_suggests_coverage_without_a_number(net, params):
    d = northern()
    assert "raise coverage nearby" in format_alert(d, top(net, params, d), None, NOW, params)


def test_zero_idle_cars_gives_no_suggestion(net, params):
    d = northern()
    assert "Suggest" not in format_alert(d, top(net, params, d), 0, NOW, params)


def test_no_stations_in_area(params):
    assert "No stations in your area" in format_alert(northern(), [], 6, NOW, params)


def test_line_wide_text():
    d = northern(severity_class="severe", line_wide=True, affected_station_ids=())
    assert format_line_wide(d) == "ℹ️ Northern line severe delays (unplanned, 18:07) across the line. TfL gave no section, so no station suggestion."

"""T12 look-ahead, on TfL's real 7-day status for 24–30 Sep 2026 plus synthetic cases."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from surgesignal.alerts.lookahead import closure_items, event_items, format_lookahead
from surgesignal.engine.types import Event, ServiceArea
from surgesignal.inputs.baseline import Baseline
from surgesignal.tfl.parser import Incident, parse_status

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "tfl" / "future_status_2026-09-24_to_2026-09-30.json"
NOW = datetime(2026, 9, 24, 17, 0, tzinfo=timezone.utc)  # Thursday 18:00 London


@pytest.fixture(scope="module")
def future(net):
    return parse_status(json.loads(FIXTURE.read_text()), net)


@pytest.fixture(scope="module")
def baseline():
    return Baseline.load()


def area(net, station, km):
    s = [st for st in net.stations.values() if st.name == station][0]
    return ServiceArea(s.lat, s.lon, km)


def lines(net):
    return {lid: line.station_ids for lid, line in net.lines.items()}


def items_for(net, params, baseline, future, station, km):
    return closure_items(future.incidents, net.stations, lines(net), baseline.at, area(net, station, km), NOW, params)


# ---- parsing the real 7-day status ----------------------------------------------------


def test_real_future_status_parses_cleanly(future):
    # "between 0210 and 0530" is a time range; "Use DISTRICT LINE trains between…" is advice;
    # "Hainault (via Newbury Park)" is a bracketed routing hint
    assert future.problems == []


def test_bracketed_via_hint_follows_the_right_branch(net, future):
    (night,) = [i for i in future.incidents if i.line == "central" and "Marble Arch and Loughton" in i.reason]
    names = {net.stations[s].name for s in night.station_ids}
    assert {"Marble Arch", "Loughton", "Newbury Park", "Hainault"} <= names and "Chigwell" not in names


def test_waterloo_and_city_weekend_is_not_a_disruption(future):
    assert not [i for i in future.incidents if i.line == "waterloo-city"]


def test_where_trains_still_run_is_not_closed(net, future):
    picc = {net.stations[s].name for i in future.incidents if i.line == "piccadilly" for s in i.station_ids}
    assert "Cockfosters" in picc and "Heathrow Terminal 5" not in picc  # "trains continue to operate … Heathrow"


def test_no_service_section_outranks_the_headline(future):
    district = {i.severity_class for i in future.incidents if i.line == "district"}
    assert district == {"part_suspended", "severe"}  # "No service between Embankment and Whitechapel" + "SEVERE DELAYS…"


def test_scheduled_periods_are_parsed(future):
    (weekend,) = [i for i in future.incidents if i.line == "central" and "Liverpool Street and Woodford" in i.reason]
    assert weekend.periods == ((datetime(2026, 9, 26, 4, 30, tzinfo=timezone.utc), datetime(2026, 9, 28, 0, 29, tzinfo=timezone.utc)),)
    assert not weekend.is_unplanned


# ---- closure items --------------------------------------------------------------------


def test_one_item_per_closure_not_per_section(net, params, baseline, future):
    items = items_for(net, params, baseline, future, "Liverpool Street", 5)
    titles = [i.title for i in items]
    assert len(titles) == len(set(titles)) == 3
    assert sum("Piccadilly" in t for t in titles) == 1


def test_items_show_each_place_once_and_skip_live_incidents(net, params, baseline, future):
    for item in items_for(net, params, baseline, future, "Liverpool Street", 5):
        names = [h.station.name for h in item.top]
        assert len(names) == len(set(names))
        assert item.kind == "closure"
    assert all("Debden" not in i.title for i in items_for(net, params, baseline, future, "Leytonstone", 10))


def test_busiest_moment_is_inside_the_closure(net, params, baseline, future):
    for item in items_for(net, params, baseline, future, "Liverpool Street", 5):
        assert item.starts <= item.peak_at <= item.ends


def test_far_away_area_gets_nothing(net, params, baseline, future):
    assert items_for(net, params, baseline, future, "Morden", 2) == []


def test_minor_planned_work_is_below_the_floor(net, params, baseline):
    inc = Incident("x", "victoria", "Victoria", "minor", 9, False, ("940GZZLUBXN", "940GZZLUSKW"),
                   reason="Minor delays between Brixton and Stockwell.",
                   periods=((NOW + timedelta(days=1), NOW + timedelta(days=1, hours=3)),))
    assert closure_items([inc], net.stations, lines(net), baseline.at, area(net, "Stockwell", 3), NOW, params) == []


def test_strike_is_labelled(net, params, baseline):
    inc = Incident("s", "victoria", "Victoria", "suspended", 2, False, (),
                   reason="Victoria line: no service due to strike action.", line_wide=True,
                   periods=((NOW + timedelta(days=2), NOW + timedelta(days=2, hours=20)),))
    (item,) = closure_items([inc], net.stations, lines(net), baseline.at, area(net, "Oxford Circus", 3), NOW, params)
    assert item.kind == "strike" and item.title == "Victoria line suspended (strike)"


def test_outside_the_horizon_is_ignored(net, params, baseline):
    far = NOW + timedelta(days=params.lookahead_days + 1)
    inc = Incident("f", "victoria", "Victoria", "suspended", 2, False, ("940GZZLUBXN",), periods=((far, far + timedelta(hours=5)),))
    assert closure_items([inc], net.stations, lines(net), baseline.at, area(net, "Brixton", 3), NOW, params) == []


# ---- events ---------------------------------------------------------------------------


def wembley(ends_at, capacity=90000):
    return Event("England v Brazil", 51.5560, -0.2795, capacity, ends_at)


def test_event_item_in_area(net, params, baseline):
    ends = NOW + timedelta(days=2, hours=4)
    (item,) = event_items([wembley(ends)], net.stations, baseline.at, area(net, "Wembley Park", 5), NOW, params)
    assert item.kind == "event" and item.peak_at == ends
    assert item.starts == ends - timedelta(minutes=30) and item.ends == ends + timedelta(minutes=45)
    assert "Wembley Park" in [h.station.name for h in item.top] and item.top[0].pct.mid == pytest.approx(0.3)


@pytest.mark.parametrize("where,ends_in,capacity", [("Morden", 1, 90000), ("Wembley Park", 9, 90000),
                                                    ("Wembley Park", -1, 90000), ("Wembley Park", 1, 5000)])
def test_event_filters(net, params, baseline, where, ends_in, capacity):
    e = wembley(NOW + timedelta(days=ends_in), capacity)
    assert event_items([e], net.stations, baseline.at, area(net, where, 5), NOW, params) == []


# ---- text -----------------------------------------------------------------------------


def test_format_real_weekend(net, params, baseline, future):
    items = items_for(net, params, baseline, future, "Liverpool Street", 5)
    text = format_lookahead(items, "Liverpool Street", 5, 6, 7, params)
    assert text.startswith("📅 Next 7 days near Liverpool Street (5 km):")
    assert "Central line part suspended (planned) between Liverpool Street and Woodford / Newbury Park" in text
    assert "line line" not in text and "between 0210 and 0530" not in text
    assert "Have 1 of your 6 cars nearby then." in text


def test_format_event_and_empty(net, params, baseline):
    ends = datetime(2026, 9, 26, 20, 45, tzinfo=timezone.utc)
    items = event_items([wembley(ends)], net.stations, baseline.at, area(net, "Wembley Park", 5), NOW, params)
    text = format_lookahead(items, "Wembley Park", 5, None, 7, params)
    assert "Sat 26 Sep 21:45: England v Brazil ends (90,000 capacity). 21:15–22:30: +30% around" in text
    assert "Have" not in text  # idle cars not set
    assert format_lookahead([], "X", 5, 6, 7, params).endswith("nothing planned that should move your demand.")

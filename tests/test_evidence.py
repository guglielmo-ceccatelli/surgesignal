import json
from datetime import datetime, timedelta, timezone

from surgesignal.alerts.settings import Settings
from surgesignal.evidence import (
    AreaRate,
    WeekCount,
    alerts_in,
    covered_segments,
    is_open,
    open_hours,
    rate,
    segment_snapshots,
)
from surgesignal.replay import Event
from tests.workers.test_alerter import SUSPENDED, status

T = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)  # 17:00 London


def at(minutes):
    return T + timedelta(minutes=minutes)


def test_covered_segments_split_on_long_gaps():
    times = [at(m) for m in (0, 5, 10, 60, 65, 200)]
    assert covered_segments(times) == [(at(0), at(10)), (at(60), at(65))]  # the lone 200 covers nothing
    assert covered_segments([]) == []


def test_open_hours_skip_the_night():
    assert is_open(at(0)) and not is_open(datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc))  # 03:00 London
    night = (datetime(2026, 9, 25, 1, 0, tzinfo=timezone.utc), datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc))
    assert open_hours([(at(0), at(60)), night]) == 1.0


def test_segment_carries_in_the_status_that_still_holds():
    snaps = [(at(-30), b"a"), (at(5), b"b"), (at(90), b"c")]
    assert segment_snapshots(snaps, (at(0), at(10))) == [(at(0), b"a"), (at(5), b"b")]
    assert segment_snapshots([], (at(0), at(10))) == []


def test_rate_counts_actionable_alerts_in_open_hours():
    events = [Event(at(0), "new", "⚠️ Northern line suspended"), Event(at(1), "new", "ℹ️ Jubilee line severe delays"),
              Event(datetime(2026, 9, 25, 2, 0, tzinfo=timezone.utc), "new", "⚠️ at night")]
    n, per_week, info = rate(events, hours_open=12.6)
    assert (n, info) == (1, 1) and per_week == 10.0  # 1 per 12.6 open hours × 126 open hours a week


def test_typical_rate_is_the_median_area():
    week = WeekCount("2026-09-28", "a", "b", 10, 10, 3, [AreaRate("x", 5, 1, r) for r in (14.0, 23.3, 46.6)])
    assert week.typical_per_week == 23.3
    assert json.loads(json.dumps(week.to_dict()))["typical_per_week"] == 23.3


def test_an_incident_spanning_a_gap_alerts_once(net, params):
    """One shared state across segments: the suspension is still on after the Mac wakes, so no second alert."""
    raw = status(SUSPENDED)
    snaps = [(at(0), raw)]
    s = net.stations["940GZZLUCPC"]
    settings = Settings(idle_drivers=6, area_lat=s.lat, area_lon=s.lon, area_label="Clapham Common", radius_km=5,
                        lookahead_at=None)
    alerts = alerts_in(snaps, [(at(0), at(15)), (at(120), at(135))], net, params, settings)
    assert len(alerts) == 1 and alerts[0].text.startswith("⚠️ Northern line")

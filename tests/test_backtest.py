from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from console.charts import backtest_chart
from surgesignal.backtest import Scored, busiest_in, docks_near, excess, normal, score_events, summarise, taken
from surgesignal.engine.types import ServiceArea
from surgesignal.replay import Event

T = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)  # Monday 17:00 London


def frame(rows):
    """rows: {minutes from T (may be days earlier): {dock: bikes}}"""
    idx = pd.to_datetime([T + timedelta(minutes=m) for m in rows], utc=True)
    return pd.DataFrame(list(rows.values()), index=idx).sort_index()


def steady(start, bikes, n=7, step=5, moves=0):
    """n snapshots from `start`, alternating ±moves per step."""
    return {start + i * step: {"d1": bikes + (moves if i % 2 else 0)} for i in range(n)}


def test_activity_counts_moves_both_ways():
    bikes = frame(steady(0, 10, moves=2))  # +2, -2, +2 ... over 30 min: 6 moves of 2
    assert taken(bikes, ["d1"], T) == 12.0


def test_activity_needs_the_whole_window():
    bikes = frame({0: {"d1": 5}, 30: {"d1": 7}})  # a 30-min hole: can't count activity
    assert taken(bikes, ["d1"], T) is None
    assert taken(frame(steady(0, 5)), [], T) is None
    assert taken(frame(steady(60, 5)), ["d1"], T) is None  # no snapshot soon after t


def test_normal_uses_other_days_at_the_same_hour():
    day = 24 * 60
    rows = {**steady(0, 10, moves=5), **steady(-day, 10, moves=1), **steady(-2 * day, 10, moves=1),
            **steady(-day + 300, 10, moves=9)}  # 5 h off the hour: ignored
    bikes = frame(rows)
    usual = normal(bikes, ["d1"], T)  # sliding windows, some a step short: about 1 move per step
    assert 5.0 <= usual <= 6.0
    assert excess(bikes, ["d1"], T) == 30.0 - usual
    assert normal(frame(steady(0, 10)), ["d1"], T) is None  # no other days


def test_docks_near(net):
    s = net.stations["940GZZLUWLO"]  # Waterloo
    docks = {"near": (s.lat + 0.001, s.lon), "far": (s.lat + 0.05, s.lon)}
    assert docks_near(s.lat, s.lon, docks) == ["near"]


def test_busiest_in_area_ignores_the_disruption(net):
    wlo = net.stations["940GZZLUWLO"]
    area = ServiceArea(wlo.lat, wlo.lon, 1.0)
    busy = {"940GZZLUWLO": 9.0, "940GZZLUEMB": 5.0, "940GZZLUEPG": 99.0}  # Epping: busy but far
    assert busiest_in(net, area, busy, n=2)[0] == "940GZZLUWLO" and "940GZZLUEPG" not in busiest_in(net, area, busy)


def test_score_and_summarise(net):
    wlo = net.stations["940GZZLUWLO"]
    day = 24 * 60
    bikes = frame({**steady(0, 10, moves=4), **steady(-day, 10, moves=1), **steady(-2 * day, 10, moves=1)})
    docks = {"d1": (wlo.lat, wlo.lon)}
    e = Event(T, "new", "⚠️ Bakerloo line suspended (unplanned, 17:00).",
              top_ids={"bakerloo:x": ("940GZZLUWLO",)}, sections={"bakerloo:x": ("940GZZLUWLO", "940GZZLUEMB")})
    (s,) = score_events([e], net, ServiceArea(wlo.lat, wlo.lon, 2), bikes, docks, lambda t: {"940GZZLUWLO": 1.0})
    assert 18.0 <= s.engine <= 19.0 and s.closed == s.engine and s.label.startswith("Bakerloo line suspended, Mon 28 Sep")
    summary = summarise([s, Scored("x", "y", 1.0, None, 2.0)])
    assert summary["measured"] == 1 and summary["events"] == 2
    assert {r["rule"] for r in summary["rules"]} == {"engine", "closed", "busiest"}


def test_summary_interval_and_beats():
    rows = [Scored(str(i), str(i), e, c, b) for i, (e, c, b) in enumerate([(3, 1, 5), (2, 2, 1), (4, 0, 0)])]
    s = summarise(rows)
    engine = next(r for r in s["rules"] if r["rule"] == "engine")
    assert engine["mean"] == 3.0 and engine["low"] <= 3.0 <= engine["high"]
    assert s["engine_beats"] == {"closed": 2, "busiest": 2}


def test_chart_draws_three_rows():
    bt = summarise([Scored(str(i), f"event {i}", e, c, b) for i, (e, c, b) in enumerate([(3, 1, 5), (2, 2, 1)])])
    fig = backtest_chart(bt)
    assert len(fig.data) == 9  # per rule: events, interval, mean
    assert fig.data[-1].marker.color == "#C8321E"  # SurgeSignal drawn last, in the accent


@pytest.mark.parametrize("bad", [None, []])
def test_taken_without_docks(bad):
    assert taken(frame(steady(0, 1)), bad or [], T) is None

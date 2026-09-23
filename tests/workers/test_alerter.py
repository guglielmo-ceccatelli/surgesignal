"""End to end: TfL status JSON → alerter → fake Telegram, with real parser, tracker, engine, lifecycle."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from surgesignal.alerts.bot import Bot
from surgesignal.alerts.telegram import TelegramError
from surgesignal.store.state import FileStore
from tests.alerts.conftest import BUILDER, DISPATCHER, NOW
from workers.alerter import Alerter, run_forever

SUSPENDED = "Northern Line: No service between Stockwell and Morden due to a signal failure. GOOD SERVICE on the rest of the line."


def status(reason=None, code=2, line="northern"):
    ls = {"statusSeverity": 10, "reason": "", "validityPeriods": []} if reason is None else {
        "statusSeverity": code, "reason": reason, "validityPeriods": [], "disruption": {"category": "RealTime"}}
    return json.dumps([{"id": line, "lineStatuses": [ls]}]).encode()


class Feed:
    def __init__(self, payload):
        self.payload = payload

    def __call__(self, url):
        assert url.endswith("/Line/Mode/tube,elizabeth-line/Status")
        return self.payload


@pytest.fixture
def store(tmp_path):
    s = FileStore(tmp_path)
    s.put_state("settings", {"idle_drivers": 6, "area_lat": 51.461742, "area_lon": -0.138317,
                             "area_label": "Clapham Common", "radius_km": 5.0})
    return s


def alerter(cfg, store, tg, net, params, feed):
    return Alerter(cfg, store, tg, net, params, fetch=feed)


def test_no_area_set_means_no_alerts_but_incidents_are_tracked(cfg, tmp_path, tg, net, params):
    s = FileStore(tmp_path)
    assert alerter(cfg, s, tg, net, params, Feed(status(SUSPENDED))).tick(NOW) == []
    assert tg.sent == [] and len(s.get_state("tracker")) == 1


def test_suspension_alerts_once_survives_restart_and_resolves(cfg, store, tg, net, params):
    feed = Feed(status(SUSPENDED))
    a = alerter(cfg, store, tg, net, params, feed)
    a.tick(NOW)
    # alerts on the FIRST tick that sees it: the forecast looks to the end of the demand ramp
    assert [m["chat"] for m in tg.sent] == [DISPATCHER]
    assert tg.sent[0]["text"].startswith("⚠️ Northern line suspended (unplanned, 18:17).")

    a.tick(NOW + timedelta(minutes=1))
    restarted = alerter(cfg, store, tg, net, params, feed)  # a crash + auto-restart
    restarted.tick(NOW + timedelta(minutes=2))
    assert len(tg.sent) == 1  # no duplicates

    feed.payload = status()  # good service again
    restarted.tick(NOW + timedelta(minutes=3))
    restarted.tick(NOW + timedelta(minutes=10))
    assert len(tg.sent) == 1  # inside the merge window: no premature all-clear
    restarted.tick(NOW + timedelta(minutes=13))
    assert tg.sent[-1]["text"] == "✅ Northern line: back to good service, since 18:20."
    assert tg.sent[-1]["reply_to"] == tg.sent[0]["id"]
    restarted.tick(NOW + timedelta(minutes=20))
    assert len(tg.sent) == 2


def test_failed_send_is_retried_next_tick(cfg, store, tg, net, params):
    a = alerter(cfg, store, tg, net, params, Feed(status(SUSPENDED)))
    tg.fail_sends = 1
    with pytest.raises(TelegramError):
        a.tick(NOW)
    assert store.get_state("alerts") in (None, {})
    a.tick(NOW + timedelta(minutes=1))
    assert len(tg.sent) == 1


def test_parser_problems_reach_builder_once_a_day(cfg, store, tg, net, params):
    feed = Feed(status("Severe delays between Nowhere Junction and Brixton.", code=6, line="victoria"))
    a = alerter(cfg, store, tg, net, params, feed)
    a.tick(NOW)
    a.tick(NOW + timedelta(minutes=1))
    builder = [m for m in tg.sent if m["chat"] == BUILDER]
    assert len(builder) == 1 and "Nowhere Junction" in builder[0]["text"]


def test_errors_ping_builder_at_most_hourly(cfg, store, tg, net, params):
    a = alerter(cfg, store, tg, net, params, Feed(b"<html>"))
    for minutes in (0, 1, 61):
        a.report_error(ValueError("boom"), NOW + timedelta(minutes=minutes))
    assert len([m for m in tg.sent if m["chat"] == BUILDER]) == 2


def test_run_loop_ticks_and_handles_updates(cfg, store, tg, net, params):
    a = alerter(cfg, store, tg, net, params, Feed(status()))
    tg.pending_updates = [{"update_id": 41, "message": {"chat": {"id": DISPATCHER}, "text": "/idle 4"}}]
    run_forever(a, Bot(cfg, store, tg, net), tg, store, iterations=1, heartbeat_path=store.root / "hb.json")
    assert store.get_state("settings")["idle_drivers"] == 4
    assert store.get_state("telegram") == {"offset": 42}
    hb = json.loads((store.root / "hb.json").read_text())
    assert hb["loop"] and hb["tick_ok"]


def test_run_loop_survives_a_failing_tick(cfg, store, tg, net, params):
    a = alerter(cfg, store, tg, net, params, Feed(b"not json"))
    run_forever(a, Bot(cfg, store, tg, net), tg, store, iterations=1, heartbeat_path=store.root / "hb.json")  # must not raise
    assert any("alerter error" in m["text"] for m in tg.sent if m["chat"] == BUILDER)
    assert json.loads((store.root / "hb.json").read_text())["tick_ok"] is None  # alive, but ticks failing


class FakeWeather:
    def __init__(self, w):
        self.w, self.last_error = w, None

    def current(self, lat, lon, now):
        return self.w


def test_live_rain_reaches_the_alert(cfg, store, tg, net, params):
    from surgesignal.inputs.weather import Weather

    a = Alerter(cfg, store, tg, net, params, fetch=Feed(status(SUSPENDED)), weather=FakeWeather(Weather(3.0, 12.0)))
    a.tick(NOW)
    text = tg.sent[0]["text"]
    assert "heavy rain" in text and "+45–86%" in text
    assert "Victoria" not in text and "Vauxhall" not in text  # regression: rain alone ranked big stations top


def test_weather_outage_still_alerts_and_tells_builder(cfg, store, tg, net, params):
    a = Alerter(cfg, store, tg, net, params, fetch=Feed(status(SUSPENDED)), weather=FakeWeather(None))
    a.tick(NOW)
    a.tick(NOW + timedelta(minutes=1))
    dispatcher = [m for m in tg.sent if m["chat"] == DISPATCHER]
    builder = [m for m in tg.sent if m["chat"] == BUILDER]
    assert len(dispatcher) == 1 and "rain" not in dispatcher[0]["text"]
    assert len(builder) == 1 and "weather unavailable" in builder[0]["text"]


def test_bad_events_row_is_reported_not_fatal(cfg, store, tg, net, params, tmp_path):
    events = tmp_path / "events.csv"
    events.write_text("venue,name,ends_at\nNarnia Arena,x,2026-09-25 18:30\n")
    a = Alerter(cfg, store, tg, net, params, fetch=Feed(status(SUSPENDED)), events_path=events)
    a.tick(NOW)
    assert any(m["chat"] == DISPATCHER for m in tg.sent)
    assert any("Narnia Arena" in m["text"] for m in tg.sent if m["chat"] == BUILDER)


# ---- daily look-ahead -----------------------------------------------------------------

FUTURE = (Path(__file__).resolve().parent.parent / "fixtures" / "tfl" / "future_status_2026-09-24_to_2026-09-30.json").read_bytes()


class Routes:
    """Live status for the tick, the real 7-day fixture for the look-ahead (or an error)."""

    def __init__(self, future=FUTURE):
        self.future, self.future_calls = future, 0

    def __call__(self, url):
        if "/to/" in url:
            self.future_calls += 1
            if isinstance(self.future, Exception):
                raise self.future
            return self.future
        return status()


def lookahead_alerter(cfg, store, tg, net, params, routes, area="Liverpool Street 5"):
    from surgesignal.alerts.settings import Settings, set_area, set_idle
    store.put_state("settings", set_idle(set_area(Settings(), area, net), "6").to_dict())
    return Alerter(cfg, store, tg, net, params, fetch=routes, lookahead=True)


THU_1759 = datetime(2026, 9, 24, 16, 59, tzinfo=timezone.utc)  # 17:59 London


def test_daily_lookahead_sent_once_after_its_time(cfg, store, tg, net, params):
    routes = Routes()
    a = lookahead_alerter(cfg, store, tg, net, params, routes)
    a.tick(THU_1759)
    assert tg.sent == [] and routes.future_calls == 0  # before 18:00
    a.tick(THU_1759 + timedelta(minutes=1))
    a.tick(THU_1759 + timedelta(minutes=30))
    (m,) = tg.sent
    assert m["chat"] == DISPATCHER and m["text"].startswith("📅 Next 7 days near Liverpool Street")
    assert routes.future_calls == 1
    a.tick(THU_1759 + timedelta(days=1, minutes=1))  # next day: again
    assert routes.future_calls == 2


def test_nothing_planned_sends_nothing_but_counts_as_done(cfg, store, tg, net, params):
    routes = Routes()
    a = lookahead_alerter(cfg, store, tg, net, params, routes, area="Morden 2")
    a.tick(THU_1759 + timedelta(minutes=1))
    a.tick(THU_1759 + timedelta(minutes=2))
    assert tg.sent == [] and routes.future_calls == 1


def test_lookahead_off_or_quiet(cfg, store, tg, net, params):
    routes = Routes()
    a = lookahead_alerter(cfg, store, tg, net, params, routes)
    s = store.get_state("settings")
    store.put_state("settings", {**s, "quiet_start": "17:00", "quiet_end": "19:00"})
    a.tick(THU_1759 + timedelta(minutes=1))
    store.put_state("settings", {**s, "lookahead_at": None})
    a.tick(THU_1759 + timedelta(minutes=2))
    assert routes.future_calls == 0 and tg.sent == []


def test_tfl_failure_is_reported_and_retried(cfg, store, tg, net, params):
    routes = Routes(future=OSError("TfL down"))
    a = lookahead_alerter(cfg, store, tg, net, params, routes)
    a.tick(THU_1759 + timedelta(minutes=1))
    assert [m["chat"] for m in tg.sent] == [BUILDER] and "look-ahead failed (OSError)" in tg.sent[0]["text"]
    routes.future = FUTURE
    a.tick(THU_1759 + timedelta(minutes=2))
    assert tg.sent[-1]["chat"] == DISPATCHER and tg.sent[-1]["text"].startswith("📅")

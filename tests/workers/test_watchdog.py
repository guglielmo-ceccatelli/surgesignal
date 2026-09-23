import plistlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.launchd import plist_for
from workers.watchdog import check

T = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)  # 18:00 London


def at(minutes):
    return T + timedelta(minutes=minutes)


def logger_hb(loop=0, status=0, disruption=0, bikepoint=0):
    return {"loop": at(loop).isoformat(), "feeds": {"status": at(status).isoformat(),
                                                   "disruption": at(disruption).isoformat(),
                                                   "bikepoint": at(bikepoint).isoformat()}}


def alerter_hb(loop=0):
    return {"loop": at(loop).isoformat(), "tick_ok": at(loop).isoformat()}


def warm(minutes_ago=5, **extra):
    """State of a watchdog that last ran a few minutes ago (not the first run, no sleep)."""
    return {"last_run": at(-minutes_ago).isoformat(), "reported": {}, "restarted": {}, **extra}


def test_healthy_system_is_silent():
    v = check(T, logger_hb(), alerter_hb(), warm())
    assert v.messages == [] and v.restart == []


def test_first_run_only_records_time():
    v = check(T, None, None, {})
    assert v.messages == [] and v.restart == [] and v.state["last_run"] == T.isoformat()


def test_after_mac_sleep_skips_one_round():
    v = check(T, logger_hb(loop=-120), alerter_hb(loop=-120), warm(minutes_ago=120))
    assert v.messages == [] and v.restart == []


def test_hung_logger_is_restarted_and_reported_once():
    v = check(T, logger_hb(loop=-11), alerter_hb(), warm())
    assert v.restart == ["logger"]
    assert v.messages == ["⚠️ SurgeSignal logger stopped responding (last seen 17:49). Restarting it. "
                          "GitHub keeps backing up status and disruptions."]
    again = check(at(5), logger_hb(loop=-11), alerter_hb(loop=5), v.state)
    assert again.messages == [] and again.restart == []  # reported already; restart throttled


def test_restart_retried_after_throttle_but_not_re_reported():
    v = check(T, logger_hb(loop=-11), alerter_hb(), warm())
    later = check(at(15), logger_hb(loop=-11), alerter_hb(loop=15), {**v.state, "last_run": at(10).isoformat()})
    assert later.restart == ["logger"] and later.messages == []


def test_recovery_message():
    v = check(T, logger_hb(loop=-11), alerter_hb(), warm())
    ok = check(at(5), logger_hb(loop=5, status=5, disruption=5, bikepoint=5), alerter_hb(loop=5), v.state)
    assert ok.messages == ["✅ SurgeSignal logger back to normal."]


def test_stale_feed_with_live_logger_pings_without_restart():
    v = check(T, logger_hb(status=-11, disruption=-11), alerter_hb(), warm())
    assert v.restart == []
    assert sorted(v.messages) == [
        "⚠️ SurgeSignal TfL disruption not fetched since 17:49: logger is running, so TfL or the network is failing.",
        "⚠️ SurgeSignal TfL status not fetched since 17:49: logger is running, so TfL or the network is failing.",
    ]


def test_bikepoint_gets_a_longer_limit():
    assert check(T, logger_hb(bikepoint=-14), alerter_hb(), warm()).messages == []
    assert len(check(T, logger_hb(bikepoint=-16), alerter_hb(), warm()).messages) == 1


def test_missing_alerter_heartbeat_is_a_problem():
    v = check(T, logger_hb(), None, warm())
    assert v.restart == ["alerter"] and "alerter stopped responding (no heartbeat)" in v.messages[0]


def test_plists(tmp_path):
    logger = plist_for("logger", Path("/repo"))
    assert logger["KeepAlive"] is True and logger["ProgramArguments"] == ["/repo/.venv/bin/python", "-m", "workers.logger"]
    assert logger["WorkingDirectory"] == "/repo" and logger["StandardErrorPath"] == "/repo/data/logs/logger.log"
    watchdog = plist_for("watchdog", Path("/repo"))
    assert watchdog["StartInterval"] == 300 and "KeepAlive" not in watchdog
    plistlib.loads(plistlib.dumps(logger))  # serialises as a valid plist

import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from workers.logger import Feed, Logger, select_feeds, tfl_url

T0 = datetime(2026, 9, 23, 17, 0, tzinfo=timezone.utc)
FEEDS = (Feed("status", "/status", 60), Feed("bikepoint", "/bikes", 300))


class FakeTfL:
    """Returns a fixed body per path; paths in `failing` raise like a network error."""

    def __init__(self, failing=()):
        self.failing = set(failing)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        path = url.split("api.tfl.gov.uk")[1].split("?")[0]
        if path in self.failing:
            raise urllib.error.URLError("connection reset")
        return json.dumps({"path": path}).encode()


def test_polls_every_feed_on_first_tick_and_writes_heartbeat(tmp_path):
    fake = FakeTfL()
    Logger(tmp_path, fetch=fake, feeds=FEEDS).poll_due(0, T0)
    assert len(fake.calls) == 2
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb == {"status": T0.isoformat(), "bikepoint": T0.isoformat()}


def test_respects_each_feed_interval(tmp_path):
    fake = FakeTfL()
    logger = Logger(tmp_path, fetch=fake, feeds=FEEDS)
    logger.poll_due(0, T0)
    logger.poll_due(30, T0 + timedelta(seconds=30))    # nothing due
    logger.poll_due(60, T0 + timedelta(seconds=60))    # status only
    assert [c.split("?")[0].rsplit("/", 1)[1] for c in fake.calls] == ["status", "bikes", "status"]


def test_one_failing_feed_does_not_stop_others_or_fake_a_heartbeat(tmp_path):
    fake = FakeTfL(failing={"/bikes"})
    Logger(tmp_path, fetch=fake, feeds=FEEDS).poll_due(0, T0)
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert "status" in hb and "bikepoint" not in hb
    assert list((tmp_path / "raw" / "status").rglob("*.json.gz"))


def test_non_json_response_is_logged_not_raised(tmp_path):
    logger = Logger(tmp_path, fetch=lambda url: b"<html>error</html>", feeds=FEEDS)
    logger.poll_due(0, T0)  # must not raise
    assert json.loads((tmp_path / "heartbeat.json").read_text()) == {}


def test_heartbeat_survives_restart(tmp_path):
    Logger(tmp_path, fetch=FakeTfL(), feeds=FEEDS).poll_due(0, T0)
    restarted = Logger(tmp_path, fetch=FakeTfL(failing={"/status", "/bikes"}), feeds=FEEDS)
    restarted.poll_due(0, T0 + timedelta(minutes=5))
    assert json.loads((tmp_path / "heartbeat.json").read_text())["status"] == T0.isoformat()


def test_app_key_is_added_only_when_set():
    assert tfl_url("/BikePoint", None) == "https://api.tfl.gov.uk/BikePoint"
    assert tfl_url("/BikePoint", "abc") == "https://api.tfl.gov.uk/BikePoint?app_key=abc"


def test_select_feeds_subset_and_validation():
    assert [f.name for f in select_feeds("status, disruption")] == ["status", "disruption"]
    with pytest.raises(SystemExit):
        select_feeds("status,bogus")
    with pytest.raises(SystemExit):
        select_feeds(" , ")

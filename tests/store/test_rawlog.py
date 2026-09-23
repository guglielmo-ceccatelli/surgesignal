import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from surgesignal.store.rawlog import RawLog, content_hash, strip_volatile

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tfl"
T0 = datetime(2026, 9, 23, 17, 5, 30, tzinfo=timezone.utc)


def status_payload(severity=10, modified="2026-09-15T13:26:22.117Z"):
    return json.dumps([{
        "id": "northern",
        "created": "2026-09-15T13:26:22.117Z",
        "modified": modified,
        "lineStatuses": [{"statusSeverity": severity, "created": "0001-01-01T00:00:00"}],
    }]).encode()


def test_strip_volatile_removes_nested_timestamp_keys():
    obj = {"created": 1, "a": [{"modified": 2, "keep": 3}], "b": {"created": 4, "c": 5}}
    assert strip_volatile(obj) == {"a": [{"keep": 3}], "b": {"c": 5}}


def test_hash_ignores_volatile_fields_and_key_order():
    a = {"id": "x", "modified": "t1", "s": 10}
    b = {"s": 10, "modified": "t2", "id": "x"}
    assert content_hash(a) == content_hash(b)


def test_hash_changes_when_status_changes():
    assert content_hash(json.loads(status_payload(10))) != content_hash(json.loads(status_payload(20)))


def test_first_save_writes_gzip_with_exact_bytes(tmp_path):
    payload = status_payload()
    path = RawLog(tmp_path, "status").save_if_changed(payload, T0)
    assert path == tmp_path / "status" / "2026-09-23" / "170530Z.json.gz"
    assert gzip.decompress(path.read_bytes()) == payload


def test_identical_payload_is_skipped(tmp_path):
    log = RawLog(tmp_path, "status")
    assert log.save_if_changed(status_payload(), T0) is not None
    assert log.save_if_changed(status_payload(), T0 + timedelta(minutes=1)) is None


def test_timestamp_only_change_is_skipped(tmp_path):
    log = RawLog(tmp_path, "status")
    log.save_if_changed(status_payload(modified="2026-09-15T13:26:22Z"), T0)
    assert log.save_if_changed(status_payload(modified="2026-09-23T17:06:00Z"), T0 + timedelta(minutes=1)) is None


def test_real_change_is_saved(tmp_path):
    log = RawLog(tmp_path, "status")
    log.save_if_changed(status_payload(10), T0)
    assert log.save_if_changed(status_payload(20), T0 + timedelta(minutes=1)) is not None
    assert len(list((tmp_path / "status" / "2026-09-23").glob("*.json.gz"))) == 2


def test_restart_remembers_last_hash(tmp_path):
    RawLog(tmp_path, "status").save_if_changed(status_payload(), T0)
    restarted = RawLog(tmp_path, "status")
    assert restarted.save_if_changed(status_payload(), T0 + timedelta(minutes=1)) is None


def test_non_json_payload_is_rejected_and_not_stored(tmp_path):
    log = RawLog(tmp_path, "status")
    with pytest.raises(ValueError, match="not JSON"):
        log.save_if_changed(b"<html>503 Service Unavailable</html>", T0)
    assert not list((tmp_path / "status").rglob("*.gz"))


def test_naive_datetime_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        RawLog(tmp_path, "status").save_if_changed(status_payload(), datetime(2026, 9, 23))


def test_non_utc_time_is_stored_under_utc_filename(tmp_path):
    bst = timezone(timedelta(hours=1))
    path = RawLog(tmp_path, "status").save_if_changed(status_payload(), datetime(2026, 9, 23, 0, 30, tzinfo=bst))
    assert path.parent.name == "2026-09-22" and path.name == "233000Z.json.gz"


def test_real_tfl_fixture_hashes_stably():
    raw = (FIXTURES / "status_2026-09-23_central-minor-delays.json").read_bytes()
    assert content_hash(json.loads(raw)) == content_hash(json.loads(raw))

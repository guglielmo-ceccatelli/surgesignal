import json
from datetime import datetime, timedelta, timezone

from surgesignal.store.state import Checkin, FileStore, SupabaseStore

T = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)


def test_filestore_state_round_trip(tmp_path):
    s = FileStore(tmp_path)
    assert s.get_state("alerts") is None
    s.put_state("alerts", {"a": 1})
    assert FileStore(tmp_path).get_state("alerts") == {"a": 1}


def test_filestore_checkins_since(tmp_path):
    s = FileStore(tmp_path)
    s.add_checkin(Checkin("x", "busy", T - timedelta(hours=1), "h"))
    s.add_checkin(Checkin("y", "dead", T, "h"))
    assert [c.station_id for c in s.checkins_since(T - timedelta(minutes=30))] == ["y"]


def test_filestore_disruptions(tmp_path):
    FileStore(tmp_path).put_disruptions([{"key": "k"}])
    assert json.loads((tmp_path / "disruptions.json").read_text()) == [{"key": "k"}]


class FakeHttp:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, json.loads(body) if body else None))
        return self.responses.pop(0) if self.responses else b""


def store(http):
    return SupabaseStore("https://abc.supabase.co/", "SERVICE", http)


def test_supabase_get_state_and_auth_headers():
    http = FakeHttp(b'[{"value": {"a": 1}}]', b"[]")
    s = store(http)
    assert s.get_state("alerts") == {"a": 1}
    assert s.get_state("missing") is None
    method, url, headers, _ = http.calls[0]
    assert (method, url) == ("GET", "https://abc.supabase.co/rest/v1/state?name=eq.alerts&select=value")
    assert headers["apikey"] == "SERVICE" and headers["Authorization"] == "Bearer SERVICE"


def test_supabase_put_state_upserts():
    http = FakeHttp()
    store(http).put_state("alerts", {"a": 1})
    method, url, headers, body = http.calls[0]
    assert method == "POST" and url.endswith("/rest/v1/state")
    assert "resolution=merge-duplicates" in headers["Prefer"] and body["name"] == "alerts" and body["value"] == {"a": 1}


def test_supabase_checkins():
    row = Checkin("x", "busy", T, "h").to_row()
    http = FakeHttp(b"", json.dumps([row]).encode())
    s = store(http)
    s.add_checkin(Checkin("x", "busy", T, "h"))
    assert s.checkins_since(T - timedelta(minutes=1)) == [Checkin("x", "busy", T, "h")]
    assert http.calls[1][1].startswith("https://abc.supabase.co/rest/v1/checkins?ts=gte.2026-09-25T16%3A59%3A00%2B00%3A00")


def test_supabase_disruptions_snapshot_replaces_all():
    http = FakeHttp()
    store(http).put_disruptions([{"key": "k"}])
    assert [(c[0], c[1].split("/rest/v1")[1]) for c in http.calls] == [
        ("DELETE", "/disruptions_current?key=not.is.null"), ("POST", "/disruptions_current")]
    store(http).put_disruptions([])
    assert http.calls[-1][0] == "DELETE"

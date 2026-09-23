import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from surgesignal.engine import model
from surgesignal.engine.params import load_params
from surgesignal.engine.types import Conditions, ServiceArea
from surgesignal.tfl.network import load_network
from surgesignal.tfl.parser import Incident, parse_status
from surgesignal.tfl.tracker import IncidentTracker

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tfl"
T = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def params():
    return load_params()


def inc(key="victoria:abc", started_at=None, severity="severe"):
    return Incident(key, "victoria", "Victoria", severity, 6, True, ("940GZZLUBXN", "940GZZLUSKW"),
                    started_at=started_at)


def at(minutes):
    return T + timedelta(minutes=minutes)


def test_t0_is_first_poll_seen(params):
    tr = IncidentTracker(params)
    tr.update([inc()], at(0))
    (d,) = tr.update([inc()], at(5))
    assert d.t0 == at(0) and d.resolved_at is None


def test_t0_uses_tfl_start_time_when_earlier(params):
    (d,) = IncidentTracker(params).update([inc(started_at=at(-12))], at(0))
    assert d.t0 == at(-12)


def test_future_tfl_start_time_is_clamped_to_now(params):
    (d,) = IncidentTracker(params).update([inc(started_at=at(30))], at(0))
    assert d.t0 == at(0)


def test_missing_incident_is_resolved_then_forgotten(params):
    tr = IncidentTracker(params)
    tr.update([inc()], at(0))
    (d,) = tr.update([], at(20))
    assert d.resolved_at == at(20)
    assert tr.update([], at(20 + 91)) == []


def test_flap_within_merge_window_keeps_t0(params):
    tr = IncidentTracker(params)
    tr.update([inc()], at(0))
    tr.update([], at(20))
    (d,) = tr.update([inc()], at(29))
    assert d.t0 == at(0) and d.resolved_at is None


def test_return_after_merge_window_is_a_new_incident(params):
    tr = IncidentTracker(params)
    tr.update([inc()], at(0))
    tr.update([], at(20))
    (d,) = tr.update([inc()], at(31))
    assert d.t0 == at(31)


def test_severity_escalation_updates_same_incident(params):
    tr = IncidentTracker(params)
    tr.update([inc(severity="minor")], at(0))
    (d,) = tr.update([inc(severity="suspended")], at(10))
    assert d.t0 == at(0) and d.severity_class == "suspended"


def test_state_round_trips_for_restarts(params):
    tr = IncidentTracker(params)
    tr.update([inc(started_at=at(-3)), inc(key="victoria:other")], at(0))
    tr.update([inc(started_at=at(-3))], at(5))
    restored = IncidentTracker.from_dict(params, json.loads(json.dumps(tr.to_dict())))
    assert restored.state == tr.state


def test_naive_time_rejected(params):
    with pytest.raises(ValueError):
        IncidentTracker(params).update([], datetime(2026, 9, 25, 17, 0))


def test_real_tfl_json_to_ranked_hotspots(params):
    """Replay: today's real Central line status → parser → tracker → engine."""
    net = load_network()
    payload = json.loads((FIXTURES / "status_2026-09-23_central-minor-delays.json").read_text())
    parsed = parse_status(payload, net)
    now = datetime(2026, 9, 23, 15, 50, tzinfo=timezone.utc)
    disruptions = IncidentTracker(params).update(parsed.incidents, now)
    leytonstone = net.stations["940GZZLULYS"]
    area = ServiceArea(leytonstone.lat, leytonstone.lon, 5)
    result = model.run(net.stations, disruptions, Conditions(), {s: 1.0 for s in net.stations}, area, now, params)
    by_name = {h.station.name: h for h in result.hotspots}
    assert by_name["Leytonstone"].uplift.mid == pytest.approx(0.03 * 1.3)  # minor, unplanned, in section
    assert by_name["Leytonstone"].explanation == "Central line minor delays (unplanned)"
    assert by_name["Leytonstone"].uplift.mid < params.alert_threshold_uplift  # minor delays: no alert

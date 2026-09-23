"""T7: replay TfL status snapshots through the real alerter (parser → tracker → engine → lifecycle).

Two kinds of input:
  * REAL: the status snapshots the logger recorded on 2026-09-23 (Central + Metropolitan minor delays).
  * DERIVED: that same real JSON with only the Northern line's status changed over time, to drive a
    full suspension → escalation → all-clear sequence TfL didn't happen to produce that day.
"""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from surgesignal.alerts.settings import Settings, set_area, set_idle
from surgesignal.replay import load_snapshots, replay

REAL = Path(__file__).resolve().parent.parent / "fixtures" / "replay" / "status"
REASON = "Northern Line: No service between Stockwell and Morden due to a signal failure. GOOD SERVICE on the rest of the line."
T = datetime(2026, 9, 23, 17, 0, tzinfo=timezone.utc)  # 18:00 London


@pytest.fixture(scope="module")
def base():
    """Today's real status JSON with every line set to Good Service."""
    (_, raw), *_ = load_snapshots(REAL)
    payload = json.loads(raw)
    for line in payload:
        for ls in line["lineStatuses"]:
            ls.update(statusSeverity=10, statusSeverityDescription="Good Service", reason=None, disruption=None)
    return payload


def northern(base, code=None):
    """Snapshot bytes: base, with the Northern line disrupted (code) or good (None)."""
    payload = copy.deepcopy(base)
    if code is not None:
        (line,) = [l for l in payload if l["id"] == "northern"]
        line["lineStatuses"][0].update(statusSeverity=code, statusSeverityDescription="x", reason=REASON,
                                       disruption={"category": "RealTime", "affectedStops": []})
    return json.dumps(payload).encode()


def at(minutes):
    return T + timedelta(minutes=minutes)


def settings(net, area="Clapham Common 5", idle="6"):
    return set_idle(set_area(Settings(), area, net), idle)


def summary(result):
    return [(e.at, e.kind) for e in result.events]


@pytest.fixture(scope="module")
def suspension(base):
    return [(at(0), northern(base)), (at(5), northern(base, 3)), (at(20), northern(base, 2)), (at(50), northern(base))]


def test_real_recorded_day_is_silent_and_clean(net, params):
    snapshots = load_snapshots(REAL)
    result = replay(snapshots, net, params, settings(net, "Leytonstone 5"))
    assert [s[0].strftime("%H:%M") for s in snapshots] == ["15:46", "16:36"]
    assert result.events == []  # minor delays: below threshold; no parser problems, no errors
    span = snapshots[-1][0] + timedelta(minutes=30) - snapshots[0][0]  # 15:46:16 → 17:06:09
    assert result.ticks == span // timedelta(minutes=1) + 1


def test_suspension_alerts_once_escalates_and_clears(net, params, suspension):
    result = replay(suspension, net, params, settings(net))
    assert summary(result) == [(at(5), "new"), (at(20), "edit"), (at(60), "resolved")]
    new, edit, resolved = result.events
    assert new.text.startswith("⚠️ Northern line part suspended (unplanned, 18:05).")
    assert "move 1 of your 6 idle cars" in new.text
    assert edit.text.startswith("⚠️ Northern line suspended (unplanned, 18:05).")  # same incident, same t0
    assert resolved.text == "✅ Northern line: back to good service, since 18:50."


def test_restart_mid_disruption_changes_nothing(net, params, suspension):
    plain = replay(suspension, net, params, settings(net))
    restarted = replay(suspension, net, params, settings(net), restart_at=[at(10), at(30), at(55)])
    assert summary(restarted) == summary(plain)


def test_flapping_status_is_one_incident(net, params, base):
    flap = [(at(0), northern(base)), (at(5), northern(base, 2)), (at(50), northern(base)),
            (at(55), northern(base, 2)), (at(90), northern(base))]
    result = replay(flap, net, params, settings(net))
    # one incident: one alert, one all-clear (edits may happen as the evening's busiest stations shift)
    assert [(e.at, e.kind) for e in result.events if e.kind != "edit"] == [(at(5), "new"), (at(100), "resolved")]
    assert result.events[-1].text.endswith("since 19:30.")


def test_top_stations_follow_real_busyness_not_the_alphabet(net, params, suspension):
    new = replay(suspension, net, params, settings(net)).of("new")[0].text
    # Morden branch, Clapham Common 5 km, Wednesday 18:05: the busiest affected stations lead
    assert "around Tooting Broadway, Balham, Tooting Bec" in new
    assert "Wednesday evening peak" in new


def test_outside_the_area_is_silent(net, params, suspension):
    assert replay(suspension, net, params, settings(net, "Leytonstone 3")).events == []


def test_tfl_error_mid_replay_is_survived(net, params, base, suspension):
    broken = sorted([*suspension, (at(10), b"<html>502 Bad Gateway</html>"), (at(11), northern(base, 3))])
    result = replay(broken, net, params, settings(net))
    assert [e.kind for e in result.events] == ["new", "error", "edit", "resolved"]
    assert result.of("error")[0].at == at(10)


def test_quiet_hours_hold_the_alert_until_they_end(net, params, suspension):
    quiet = Settings(**{**settings(net).to_dict(), "quiet_start": "18:00", "quiet_end": "18:10"})
    result = replay(suspension, net, params, quiet)
    assert summary(result)[0] == (at(10), "new")  # 18:05 disruption announced at 18:10
    assert len(result.of("new")) == 1


def test_no_snapshots_is_an_empty_result(net, params):
    assert replay([], net, params, settings(net)).events == []

from dataclasses import replace
from datetime import datetime, time, timedelta, timezone

from surgesignal.alerts import lifecycle
from surgesignal.alerts.lifecycle import AlertRecord, EditExisting, SendNew, SendResolved, decide, in_quiet_hours
from tests.alerts.conftest import NOW, make_ctx, northern


def run_once(net, params, disruptions, records, now=NOW, **ctx_kw):
    return decide(disruptions, records, make_ctx(net, params, disruptions, now=now, **ctx_kw), now, params)


def sent(actions, records, now=NOW, message_id=500):
    for a in actions:
        records = lifecycle.apply(records, a, message_id, now)
    return records


def test_qualifying_suspension_sends_one_new_alert(net, params):
    (a,) = run_once(net, params, [northern()], {})
    assert isinstance(a, SendNew) and a.keys == ("northern:morden",)
    assert a.text.startswith("⚠️ Northern line suspended (unplanned, 18:07).")
    assert "Balham, Clapham Common, Clapham North" in a.text  # uniform baseline → ties by name
    assert "move 1 of your 6 idle cars" in a.text
    assert a.top_ids["northern:morden"] == ("940GZZLUBLM", "940GZZLUCPC", "940GZZLUCPN")


def test_minor_delays_below_threshold_send_nothing(net, params):
    assert run_once(net, params, [northern(severity_class="minor")], {}) == []


def test_nothing_repeats_once_sent(net, params):
    records = sent(run_once(net, params, [northern()], {}), {})
    assert run_once(net, params, [northern()], records, now=NOW + timedelta(minutes=30)) == []


def test_severity_change_edits_immediately(net, params):
    records = sent(run_once(net, params, [northern(severity_class="part_suspended")], {}), {})
    (a,) = run_once(net, params, [northern()], records, now=NOW + timedelta(minutes=1))
    assert isinstance(a, EditExisting) and a.message_id == 500 and "suspended (unplanned" in a.text


def test_top_station_change_waits_for_cooldown(net, params):
    records = sent(run_once(net, params, [northern()], {}), {})
    dead = frozenset({"940GZZLUBLM"})  # driver reported Balham dead → drops out of the top 3
    assert run_once(net, params, [northern()], records, now=NOW + timedelta(minutes=5), suppressed=dead) == []
    (a,) = run_once(net, params, [northern()], records, now=NOW + timedelta(minutes=10), suppressed=dead)
    assert isinstance(a, EditExisting) and "Balham" not in a.text


def test_resolution_waits_for_merge_window_then_replies(net, params):
    records = sent(run_once(net, params, [northern()], {}), {})
    resolved = northern(resolved_at=NOW + timedelta(minutes=20))
    assert run_once(net, params, [resolved], records, now=NOW + timedelta(minutes=25)) == []
    (a,) = run_once(net, params, [resolved], records, now=NOW + timedelta(minutes=30))
    assert isinstance(a, SendResolved) and a.reply_to == 500
    assert a.text == "✅ Northern line: back to good service, since 18:37."
    records = sent([a], records, now=NOW + timedelta(minutes=30))
    assert records["northern:morden"].state == "resolved"
    assert run_once(net, params, [resolved], records, now=NOW + timedelta(minutes=40)) == []


def test_forgotten_incident_is_resolved(net, params):
    records = sent(run_once(net, params, [northern()], {}), {})
    (a,) = run_once(net, params, [], records, now=NOW + timedelta(hours=3))
    assert isinstance(a, SendResolved) and a.text == "✅ Northern line: back to good service."


def test_incident_returning_after_resolution_is_new(net, params):
    records = sent(run_once(net, params, [northern()], {}), {})
    records = {k: replace(r, state="resolved") for k, r in records.items()}
    (a,) = run_once(net, params, [northern(t0=NOW)], records, now=NOW + timedelta(minutes=9))
    assert isinstance(a, SendNew)


def test_simultaneous_new_incidents_share_one_message(net, params):
    victoria = northern(key="victoria:bxn", line="victoria", line_name="Victoria",
                        affected_station_ids=("940GZZLUBXN", "940GZZLUSKW"))
    (a,) = run_once(net, params, [northern(), victoria], {})
    assert set(a.keys) == {"northern:morden", "victoria:bxn"} and a.text.count("⚠️") == 2
    records = sent([a], {})
    assert records["victoria:bxn"].message_id == records["northern:morden"].message_id
    # escalating one re-renders the whole shared message
    (e,) = run_once(net, params, [northern(severity_class="severe"), victoria], records, now=NOW + timedelta(minutes=1))
    assert isinstance(e, EditExisting) and set(e.keys) == {"northern:morden", "victoria:bxn"}


def test_line_wide_severe_in_area_alerts_without_stations(net, params):
    d = northern(key="northern:all", severity_class="severe", line_wide=True, affected_station_ids=())
    (a,) = run_once(net, params, [d], {})
    assert "across the line" in a.text and a.top_ids["northern:all"] == ()


def test_line_wide_minor_or_out_of_area_is_silent(net, params):
    minor = northern(key="n", severity_class="minor", line_wide=True, affected_station_ids=())
    metro = northern(key="m", line="metropolitan", line_name="Metropolitan", line_wide=True, affected_station_ids=())
    assert run_once(net, params, [minor, metro], {}) == []


def test_area_uncertain_is_flagged_in_text(net, params):
    d = northern(area_uncertain=True, severity_scale=0.5,
                 affected_station_ids=tuple(net.lines["northern"].station_ids))
    (a,) = run_once(net, params, [d], {})
    assert "Area uncertain" in a.text


def test_quiet_hours_hold_everything(net, params):
    quiet = (time(18, 0), time(19, 0))  # NOW is 18:17 London
    assert run_once(net, params, [northern()], {}, quiet=quiet) == []


def test_quiet_hours_wrap_midnight():
    q = (time(23, 0), time(6, 0))
    assert in_quiet_hours(datetime(2026, 9, 25, 23, 30, tzinfo=timezone.utc), q)  # 00:30 London
    assert not in_quiet_hours(datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc), q)
    assert not in_quiet_hours(NOW, None)


def test_prune_keeps_active_and_recent(net, params):
    records = sent(run_once(net, params, [northern()], {}), {})
    old = {"old": replace(records["northern:morden"], key="old", state="resolved",
                                    last_sent_at=NOW - timedelta(days=3))}
    kept = lifecycle.prune({**records, **old}, NOW)
    assert set(kept) == {"northern:morden"}


def test_record_round_trip(net, params):
    records = sent(run_once(net, params, [northern()], {}), {})
    rec = records["northern:morden"]
    assert AlertRecord.from_dict(rec.to_dict()) == rec
    assert rec.last_sent_at == NOW and rec.message_id == 500 and rec.group == ("northern:morden",)

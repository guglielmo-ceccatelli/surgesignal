import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from console import components, maps
from surgesignal.console import (
    PRESETS,
    REPLAYS_DIR,
    Action,
    live_view,
    ordered_stations,
    params_rows,
    read_heartbeat,
    replay_story,
    run_preset,
    section_paths,
    station_by_name,
    whatif,
)
from surgesignal.engine.types import ServiceArea
from surgesignal.inputs.baseline import Baseline
from surgesignal.store.state import Checkin, FileStore

FRI_18 = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)  # Friday 18:00 London
APP = Path(__file__).resolve().parents[2] / "console" / "app.py"
TABS = ["Overview", "Try a disruption", "The economics", "How it works", "Evidence", "Live"]


@pytest.fixture(scope="module")
def baseline():
    return Baseline.load()


def clapham(net, km=5):
    s = net.stations["940GZZLUCPC"]
    return ServiceArea(s.lat, s.lon, km)


def northern(net, params, baseline, km=5, rain="Heavy rain"):
    return whatif(net, params, baseline, "northern", "940GZZLUSKW", "940GZZLUMDN", "suspended", True,
                  FRI_18, rain, clapham(net, km), 6)


# ---- engine glue ----------------------------------------------------------------------


def test_ordered_stations_follow_the_line(net):
    names = [s.name for s in ordered_stations(net, "northern")]
    i = [names.index(n) for n in ("Stockwell", "Clapham North", "Clapham Common", "Morden")]
    assert i == sorted(i) or i == sorted(i, reverse=True)  # running order, either direction
    assert len(names) == len(set(names))


def test_section_paths_are_one_run_in_order(net):
    section = {station_by_name(net, n, "northern").id for n in ("Stockwell", "Clapham North", "Clapham Common")}
    (path,) = section_paths(net, "northern", section)
    assert [s.name for s in path] in (["Stockwell", "Clapham North", "Clapham Common"],
                                      ["Clapham Common", "Clapham North", "Stockwell"])


def test_whatif_frames_alert_and_action(net, params, baseline):
    r = northern(net, params, baseline)
    assert len(r.section) == 10 and r.paths
    assert {f["minutes"] for f in r.frames} == {"+8 min", "+15 min", "+30 min", "+60 min"}
    assert r.alert.startswith("⚠️ Northern line suspended (unplanned, 18:00).") and "heavy rain" in r.alert
    assert r.action.alerting and 1 <= r.action.cars <= 6
    assert sum(n for _, n in r.action.split) == r.action.cars
    assert r.caption.startswith("Busiest at +8 min:")


def test_whatif_ripple_spreads(net, params, baseline):
    r = northern(net, params, baseline, km=8, rain="Dry")
    oval = {f["minutes"]: f["demand_pct"] for f in r.frames if f["station"] == "Oval"}
    assert oval["+60 min"] > oval["+8 min"]


def test_whatif_rejects_stations_not_on_one_route(net, params, baseline):
    with pytest.raises(ValueError):
        whatif(net, params, baseline, "northern", "940GZZLUEGW", "940GZZLUMHL", "suspended", True,  # Edgware ↔ Mill Hill East
               FRI_18, "Dry", clapham(net), 6)


def test_whatif_outside_the_area_has_no_action(net, params, baseline):
    far = net.stations["940GZZLUEPG"]  # Epping, nowhere near the Northern line's south end
    r = whatif(net, params, baseline, "northern", "940GZZLUSKW", "940GZZLUMDN", "suspended", True,
               FRI_18, "Dry", ServiceArea(far.lat, far.lon, 2), 6)
    assert r.action is None and "doesn't reach" in r.caption


@pytest.mark.parametrize("slug", sorted(PRESETS))
def test_presets_run(net, params, baseline, slug):
    r = run_preset(net, params, baseline, PRESETS[slug])
    assert r.frames and r.action is not None


def test_planned_weekend_closure_stays_below_the_alert(net, params, baseline):
    """Planned closures give riders time to re-route: demand moves, the bot stays silent."""
    r = run_preset(net, params, baseline, PRESETS["central-weekend"])
    assert not r.action.alerting


def test_station_by_name_prefers_the_line(net):
    assert station_by_name(net, "Stockwell", "northern").id in net.lines["northern"].station_ids
    with pytest.raises(KeyError):
        station_by_name(net, "Atlantis")


# ---- Overview: the recorded 23 September evening ---------------------------------------


def test_replay_story_from_committed_snapshots(net, params, baseline):
    c = station_by_name(net, "Putney Bridge")
    folder = next(p for p in REPLAYS_DIR.iterdir() if p.is_dir())
    s = replay_story(net, params, baseline, folder, "district", "Putney Bridge", ServiceArea(c.lat, c.lon, 5), 6)
    assert s.title == "District line severe delays" and s.started_london
    assert s.sent and s.sent[0][1] == "new"
    assert s.ripple.action.alerting and s.ripple.action.split[0][0] == "Earl's Court"


def test_replay_story_needs_snapshots(net, params, baseline, tmp_path):
    with pytest.raises(FileNotFoundError):
        replay_story(net, params, baseline, tmp_path, "district", "x", clapham(net), 6)


# ---- Live --------------------------------------------------------------------------------


def alerter_store(net, params, tmp_path):
    from surgesignal.config import Config
    from surgesignal.replay import RecordingTelegram
    from tests.workers.test_alerter import SUSPENDED, Feed, status
    from workers.alerter import Alerter

    store = FileStore(tmp_path)
    s = net.stations["940GZZLUCPC"]
    store.put_state("settings", {"idle_drivers": 6, "area_lat": s.lat, "area_lon": s.lon, "area_label": "Clapham Common", "radius_km": 5.0})
    Alerter(Config("t", dispatcher_chat_id=1), store, RecordingTelegram(), net, params, fetch=Feed(status(SUSPENDED))).tick(FRI_18)
    return store


def test_live_view_from_alerter_state(net, params, baseline, tmp_path):
    store = alerter_store(net, params, tmp_path / "state")
    store.add_checkin(Checkin("940GZZLUBLM", "busy", FRI_18, "h"))
    beat = tmp_path / "heartbeat.json"
    beat.write_text(json.dumps({"tick_ok": (FRI_18 + timedelta(minutes=4)).isoformat()}))
    view = live_view(store, net, params, baseline, FRI_18 + timedelta(minutes=5), beat)
    assert len(view.disruptions) == 1 and view.hotspots and len(view.checkins) == 1
    assert len({h["station"] for h in view.hotspots}) == len(view.hotspots)
    assert view.ripple.action.alerting and not view.stale


def test_live_view_is_stale_without_a_recent_heartbeat(net, params, baseline, tmp_path):
    store = alerter_store(net, params, tmp_path / "state")
    beat = tmp_path / "heartbeat.json"
    beat.write_text(json.dumps({"tick_ok": FRI_18.isoformat()}))
    assert live_view(store, net, params, baseline, FRI_18 + timedelta(minutes=10), beat).stale
    assert live_view(store, net, params, baseline, FRI_18, tmp_path / "missing.json").stale


def test_live_view_empty_store(net, params, baseline, tmp_path):
    view = live_view(FileStore(tmp_path), net, params, baseline, FRI_18)
    assert view.disruptions == [] and view.hotspots == [] and view.ripple is None


def test_read_heartbeat_tolerates_bad_files(tmp_path):
    bad = tmp_path / "h.json"
    bad.write_text("not json")
    assert read_heartbeat(bad) is None and read_heartbeat(tmp_path / "nope.json") is None


# ---- How it works ------------------------------------------------------------------------


def test_params_rows_use_badge_vocabulary():
    rows = {r["parameter"]: r for r in params_rows()}
    assert rows["lambda_growth"]["kind"] == "ESTIMATE"
    assert {r["kind"] for r in rows.values()} <= {"ESTIMATE", "EVIDENCE", "DESIGN CHOICE"}


# ---- HTML components and maps ------------------------------------------------------------


ACTION = Action(2, 6, [("Clapham Common", 1), ("Balham", 1)], ["Clapham Common", "Balham"], 20, 45, "18:40", "Northern line suspended")


def test_components_escape_text():
    html = components.phone_alert("<script>x</script>", "18:08")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "&lt;b&gt;" in components.banner("info", "<b>")


def test_action_card_states():
    assert "Move <em>2</em> of 6 idle cars" in components.action_card(ACTION)
    assert "Balham: 1" in components.action_card(ACTION)
    quiet = Action(**{**ACTION.__dict__, "alerting": False})
    assert "No alert" in components.action_card(quiet) and "Move" not in components.action_card(quiet)
    no_idle = Action(**{**ACTION.__dict__, "cars": None, "idle": None})
    assert "Raise coverage nearby" in components.action_card(no_idle)
    assert "Nothing in this area" in components.action_card(None)


def test_figure_strip_badges():
    html = components.figure_strip([("1.5×", "surge", "evidence"), ("£15", "fare", "estimate")])
    assert html.count("ss-badge evidence") == 1 and html.count("ss-badge estimate") == 1


def test_small_multiples_share_one_colour_scale(net, params, baseline):
    r = northern(net, params, baseline)
    fig = maps.small_multiples(r, ["+8 min", "+15 min", "+30 min", "+60 min"])
    layout = fig.to_dict()["layout"]
    assert all(f"map{i}" in layout for i in ("", "2", "3", "4"))
    zooms = {layout[k]["zoom"] for k in ("map", "map2", "map3", "map4")}
    assert len(zooms) == 1  # same zoom, so the maps compare
    demand = [t for t in fig.data if t.marker and t.marker.coloraxis == "coloraxis"]
    assert len(demand) == 4


def test_colour_scale_does_not_stretch_small_rises(net, params, baseline):
    r = run_preset(net, params, baseline, PRESETS["central-weekend"])
    fig = maps.single_map(r, "+8 min")
    assert fig.layout.coloraxis.cmax >= maps.CMAX_FLOOR


# ---- the app itself ----------------------------------------------------------------------


def app(tab=None, **query):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=120)
    if tab:
        at.session_state["tab"] = tab
    for k, v in query.items():
        at.query_params[k] = v
    return at.run()


@pytest.mark.parametrize("tab", TABS)
def test_each_tab_renders_without_errors(tab):
    at = app(tab)
    assert not at.exception, [e.value for e in at.exception]
    assert [t.label for t in at.tabs] == TABS


def test_scenario_link_opens_the_preset():
    at = app(scenario="district-evening")
    assert not at.exception
    assert at.pills[0].value == PRESETS["district-evening"].label


def test_public_copy_without_local_data(tmp_path, monkeypatch):
    """Streamlit Cloud has no data/ folder: Live falls back to the recording, Evidence still renders."""
    monkeypatch.setenv("SURGESIGNAL_DATA", str(tmp_path))
    at = app("Live")
    assert not at.exception
    assert any("recorded 23 September" in h.proto.body for h in at.get("html"))
    assert not app("Evidence").exception

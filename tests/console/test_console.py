from datetime import datetime, timedelta, timezone

import pytest
from pathlib import Path

from surgesignal.console import live_view, ordered_stations, params_rows, whatif
from surgesignal.engine.types import ServiceArea
from surgesignal.inputs.baseline import Baseline
from surgesignal.store.state import Checkin, FileStore

FRI_18 = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)  # Friday 18:00 London


@pytest.fixture(scope="module")
def baseline():
    return Baseline.load()


def clapham(net, km=5):
    s = net.stations["940GZZLUCPC"]
    return ServiceArea(s.lat, s.lon, km)


def test_ordered_stations_follow_the_line(net):
    names = [s.name for s in ordered_stations(net, "northern")]
    i = [names.index(n) for n in ("Stockwell", "Clapham North", "Clapham Common", "Morden")]
    assert i == sorted(i) or i == sorted(i, reverse=True)  # running order, either direction
    assert len(names) == len(set(names))


def test_whatif_frames_and_alert(net, params, baseline):
    r = whatif(net, params, baseline, "northern", "940GZZLUSKW", "940GZZLUMDN", "suspended", True,
               FRI_18, "Heavy rain", clapham(net), 6)
    assert len(r.section) == 10
    assert {f["minutes"] for f in r.frames} == {"+8 min", "+15 min", "+30 min", "+60 min"}
    assert r.alert.startswith("⚠️ Northern line suspended (unplanned, 18:00).") and "heavy rain" in r.alert


def test_whatif_ripple_spreads(net, params, baseline):
    r = whatif(net, params, baseline, "northern", "940GZZLUSKW", "940GZZLUMDN", "suspended", True,
               FRI_18, "Dry", clapham(net, 8), 6)
    oval = {f["minutes"]: f["demand_pct"] for f in r.frames if f["station"] == "Oval"}
    assert oval["+60 min"] > oval["+8 min"]


def test_whatif_rejects_stations_not_on_one_route(net, params, baseline):
    with pytest.raises(ValueError):
        whatif(net, params, baseline, "northern", "940GZZLUEGW", "940GZZLUMHL", "suspended", True,  # Edgware ↔ Mill Hill East
               FRI_18, "Dry", clapham(net), 6)


def test_live_view_from_alerter_state(net, params, baseline, tmp_path):
    from tests.workers.test_alerter import SUSPENDED, Feed, status
    from surgesignal.replay import RecordingTelegram
    from surgesignal.config import Config
    from workers.alerter import Alerter

    store = FileStore(tmp_path)
    s = net.stations["940GZZLUCPC"]
    store.put_state("settings", {"idle_drivers": 6, "area_lat": s.lat, "area_lon": s.lon, "area_label": "Clapham Common", "radius_km": 5.0})
    Alerter(Config("t", dispatcher_chat_id=1), store, RecordingTelegram(), net, params, fetch=Feed(status(SUSPENDED))).tick(FRI_18)
    store.add_checkin(Checkin("940GZZLUBLM", "busy", FRI_18, "h"))
    view = live_view(store, net, params, baseline, FRI_18 + timedelta(minutes=5))
    assert len(view.disruptions) == 1 and view.hotspots and len(view.checkins) == 1
    assert len({h["station"] for h in view.hotspots}) == len(view.hotspots)


def test_live_view_empty_store(net, params, baseline, tmp_path):
    view = live_view(FileStore(tmp_path), net, params, baseline, FRI_18)
    assert view.disruptions == [] and view.hotspots == []


def test_params_rows_cover_the_yaml():
    rows = params_rows()
    assert any(r["parameter"] == "lambda_growth" and r["source"] == "assumption" for r in rows)


def test_app_renders_without_errors():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "console" / "app.py"), default_timeout=60).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Live", "What-if simulator", "How it works", "Backtest"]
    assert any("Northern line" in c.value for c in at.code)  # the what-if alert preview rendered

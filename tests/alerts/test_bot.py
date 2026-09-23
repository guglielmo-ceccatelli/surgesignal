from datetime import timedelta

import pytest

from surgesignal.alerts.bot import Bot, driver_hash
from surgesignal.alerts.lifecycle import AlertRecord
from surgesignal.alerts.settings import Settings, SettingsError
from surgesignal.store.state import Checkin, FileStore
from tests.alerts.conftest import DISPATCHER, DRIVER, NOW, STRANGER


@pytest.fixture
def store(tmp_path):
    return FileStore(tmp_path)


def fake_postcode(pc):
    if pc.upper().replace(" ", "") == "SW47AA":
        return 51.46, -0.14
    raise SettingsError(f"Couldn't find postcode {pc.upper()}.")


@pytest.fixture
def bot(cfg, store, tg, net):
    return Bot(cfg, store, tg, net, postcode_lookup=fake_postcode)


def msg(chat, text, update_id=1):
    return {"update_id": update_id, "message": {"chat": {"id": chat}, "text": text}}


def press(chat, data, message_id=77):
    return {"update_id": 2, "callback_query": {"id": "cb1", "data": data,
                                               "message": {"chat": {"id": chat}, "message_id": message_id}}}


def last(tg):
    return tg.sent[-1]["text"]


def settings(store):
    return Settings.from_dict(store.get_state("settings"))


def with_alert(store, top=("940GZZLUSKW", "940GZZLUCPN")):
    rec = AlertRecord("northern:morden", ("northern:morden",), 500, "active", "suspended", top, NOW)
    store.put_state("alerts", {rec.key: rec.to_dict()})


# ---- access ---------------------------------------------------------------------------


def test_whoami_answers_anyone_with_their_chat_id(bot, tg):
    bot.handle(msg(STRANGER, "/whoami"), NOW)
    assert last(tg) == f"Your chat id is {STRANGER}. Add it to config.local.yaml to use SurgeSignal."


@pytest.mark.parametrize("text", ["/start", "/idle 6", "/checkin", "/status", "hello"])
def test_strangers_are_ignored(bot, tg, store, text):
    bot.handle(msg(STRANGER, text), NOW)
    assert tg.sent == [] and store.get_state("settings") is None


def test_driver_cannot_change_settings(bot, tg, store):
    bot.handle(msg(DRIVER, "/idle 50"), NOW)
    assert store.get_state("settings") is None and "/checkin" in last(tg)


def test_command_with_bot_suffix(bot, tg):
    bot.handle(msg(DISPATCHER, "/start@SurgeSignalBot"), NOW)
    assert last(tg).startswith("SurgeSignal alerts you")


# ---- settings -------------------------------------------------------------------------


def test_idle_saved(bot, store, tg):
    bot.handle(msg(DISPATCHER, "/idle 6"), NOW)
    assert settings(store).idle_drivers == 6 and "Idle cars: 6" in last(tg)


@pytest.mark.parametrize("arg", ["abc", "-1", "501", ""])
def test_bad_idle_rejected(bot, store, tg, arg):
    bot.handle(msg(DISPATCHER, f"/idle {arg}"), NOW)
    assert store.get_state("settings") is None


def test_area_by_station_with_radius(bot, store, net):
    bot.handle(msg(DISPATCHER, "/area Clapham Common 3"), NOW)
    s = settings(store)
    assert s.area_label == "Clapham Common" and s.radius_km == 3
    assert (s.area_lat, s.area_lon) == (net.stations["940GZZLUCPC"].lat, net.stations["940GZZLUCPC"].lon)


def test_area_by_station_variant_name(bot, store):
    bot.handle(msg(DISPATCHER, "/area kings cross st pancras"), NOW)
    assert settings(store).area_label == "King's Cross St. Pancras"


def test_area_by_postcode(bot, store):
    bot.handle(msg(DISPATCHER, "/area SW4 7AA 2.5"), NOW)
    s = settings(store)
    assert (s.area_label, s.area_lat, s.radius_km) == ("SW4 7AA", 51.46, 2.5)


@pytest.mark.parametrize("arg,expect", [("Atlantis", "No station called"), ("ZZ9 9ZZ", "Couldn't find postcode"),
                                        ("Stockwell 99", "Radius must be"), ("", "Usage")])
def test_bad_area_rejected(bot, store, tg, arg, expect):
    bot.handle(msg(DISPATCHER, f"/area {arg}"), NOW)
    assert store.get_state("settings") is None and expect in last(tg)


def test_quiet_hours_and_off(bot, store):
    bot.handle(msg(DISPATCHER, "/quiet 23:00-6:30"), NOW)
    assert (settings(store).quiet_start, settings(store).quiet_end) == ("23:00", "06:30")
    bot.handle(msg(DISPATCHER, "/quiet off"), NOW)
    assert settings(store).quiet is None


@pytest.mark.parametrize("arg", ["late", "25:00-06:00", "10:00-10:00"])
def test_bad_quiet_rejected(bot, store, arg):
    bot.handle(msg(DISPATCHER, f"/quiet {arg}"), NOW)
    assert store.get_state("settings") is None


def test_settings_and_status(bot, store, tg):
    bot.handle(msg(DISPATCHER, "/settings"), NOW)
    assert "Area: not set" in last(tg)
    store.put_state("tracker", {"k": {"incident": {"line_name": "Northern", "severity_class": "suspended",
                                                   "line_wide": False, "station_ids": ["a", "b"]}, "resolved_at": None}})
    store.add_checkin(Checkin("940GZZLUSKW", "busy", NOW - timedelta(minutes=5), "h"))
    store.add_checkin(Checkin("940GZZLUSKW", "dead", NOW - timedelta(hours=2), "h"))
    bot.handle(msg(DISPATCHER, "/status"), NOW)
    text = last(tg)
    assert "• Northern line suspended (2 stations)" in text
    assert "• Stockwell: busy (18:12)" in text and "dead" not in text


# ---- check-ins ------------------------------------------------------------------------


def test_checkin_without_alert(bot, tg):
    bot.handle(msg(DRIVER, "/checkin"), NOW)
    assert "No active alert" in last(tg)


def test_full_checkin_flow_stores_hashed_driver(bot, tg, store, cfg):
    with_alert(store)
    bot.handle(msg(DRIVER, "/checkin"), NOW)
    buttons = tg.sent[-1]["markup"]["inline_keyboard"]
    assert [row[0]["text"] for row in buttons] == ["Stockwell", "Clapham North"]

    bot.handle(press(DRIVER, "ci|940GZZLUSKW"), NOW)
    assert tg.edits[-1]["text"] == "How busy is Stockwell?"
    assert [b["callback_data"] for b in tg.edits[-1]["markup"]["inline_keyboard"][0]] == [
        "cs|940GZZLUSKW|busy", "cs|940GZZLUSKW|normal", "cs|940GZZLUSKW|dead"]

    bot.handle(press(DRIVER, "cs|940GZZLUSKW|busy"), NOW)
    (c,) = store.checkins_since(NOW - timedelta(minutes=1))
    assert (c.station_id, c.status) == ("940GZZLUSKW", "busy")
    assert c.driver_hash == driver_hash(cfg.driver_hash_salt, DRIVER) and str(DRIVER) not in c.driver_hash
    assert tg.edits[-1]["text"] == "Thanks: Stockwell busy (18:17)."


@pytest.mark.parametrize("data", ["cs|940GZZLUSKW|furious", "cs|NOPE|busy", "ci|NOPE", "rubbish"])
def test_bad_button_data_is_rejected(bot, tg, store, data):
    bot.handle(press(DRIVER, data), NOW)
    assert store.checkins_since(NOW - timedelta(days=1)) == [] and tg.answers[-1][1] == "That button has expired."


def test_stranger_button_press_is_ignored(bot, tg, store):
    bot.handle(press(STRANGER, "cs|940GZZLUSKW|busy"), NOW)
    assert store.checkins_since(NOW - timedelta(days=1)) == [] and tg.answers == [("cb1", None)]

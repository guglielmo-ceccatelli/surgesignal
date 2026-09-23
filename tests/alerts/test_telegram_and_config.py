import pytest

from surgesignal.alerts.telegram import Telegram, TelegramError, keyboard
from surgesignal.config import ConfigError, load_config


class FakePost:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload, timeout):
        self.calls.append((url.rsplit("/", 1)[1], payload, timeout))
        return self.responses.pop(0)


def test_send_returns_message_id_and_threads_replies():
    post = FakePost({"ok": True, "result": {"message_id": 42}})
    assert Telegram("T", post).send(1, "hi", reply_to=7) == 42
    method, payload, _ = post.calls[0]
    assert method == "sendMessage" and payload["reply_parameters"]["message_id"] == 7
    assert "reply_markup" not in payload  # None params are dropped


def test_edit_ignores_not_modified_but_raises_other_errors():
    tg = Telegram("T", FakePost({"ok": False, "description": "Bad Request: message is not modified"},
                                {"ok": False, "description": "Bad Request: message to edit not found"}))
    tg.edit(1, 2, "same")
    with pytest.raises(TelegramError, match="not found"):
        tg.edit(1, 2, "gone")


def test_updates_long_poll_uses_longer_http_timeout():
    post = FakePost({"ok": True, "result": [{"update_id": 5}]})
    assert Telegram("T", post).updates(offset=5, timeout=25) == [{"update_id": 5}]
    method, payload, http_timeout = post.calls[0]
    assert method == "getUpdates" and payload["timeout"] == 25 and payload["offset"] == 5 and http_timeout == 35


def test_keyboard_shape():
    assert keyboard([[("A", "a")]]) == {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}


# ---- config ---------------------------------------------------------------------------


def write(tmp_path, text):
    p = tmp_path / "config.local.yaml"
    p.write_text(text)
    return p


def test_config_from_file(tmp_path):
    cfg = load_config(write(tmp_path, "telegram_bot_token: abc\ndispatcher_chat_id: 11\ndriver_chat_ids: [22, 33]\n"
                                      "driver_hash_salt: 0123456789abcdef\n"), env={})
    assert cfg.role(11) == "dispatcher" and cfg.role(22) == "driver" and cfg.role(99) is None


def test_env_overrides_file(tmp_path):
    cfg = load_config(write(tmp_path, "telegram_bot_token: abc\n"),
                      env={"SURGE_TELEGRAM_BOT_TOKEN": "env", "SURGE_DRIVER_CHAT_IDS": "5,6",
                           "SURGE_DRIVER_HASH_SALT": "x" * 16})
    assert cfg.telegram_bot_token == "env" and cfg.driver_chat_ids == frozenset({5, 6})


def test_no_dispatcher_means_nobody_is_dispatcher(tmp_path):
    cfg = load_config(write(tmp_path, "telegram_bot_token: abc\n"), env={})
    assert cfg.role(0) is None


@pytest.mark.parametrize("text,match", [
    ("dispatcher_chat_id: 1\n", "telegram_bot_token missing"),
    ("telegram_bot_token: a\nsupabase_url: https://x.supabase.co\n", "both supabase_url"),
    ("telegram_bot_token: a\ndriver_chat_ids: [1]\n", "driver_hash_salt"),
])
def test_config_errors(tmp_path, text, match):
    with pytest.raises(ConfigError, match=match):
        load_config(write(tmp_path, text), env={})


def test_missing_file_uses_env_only(tmp_path):
    assert load_config(tmp_path / "nope.yaml", env={"SURGE_TELEGRAM_BOT_TOKEN": "t"}).telegram_bot_token == "t"

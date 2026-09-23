"""Fixtures shared by tests/alerts and tests/workers."""

import pytest

from surgesignal.alerts.telegram import TelegramError
from surgesignal.config import Config
from surgesignal.engine.params import load_params
from surgesignal.replay import RecordingTelegram
from surgesignal.tfl.network import load_network

DISPATCHER, DRIVER, BUILDER, STRANGER = 1001, 2002, 3003, 9999


@pytest.fixture(scope="session")
def net():
    return load_network()


@pytest.fixture(scope="session")
def params():
    return load_params()


@pytest.fixture
def cfg():
    return Config("token", dispatcher_chat_id=DISPATCHER, driver_chat_ids=frozenset({DRIVER}),
                  builder_chat_id=BUILDER, driver_hash_salt="0123456789abcdef")


class FakeTelegram(RecordingTelegram):
    """RecordingTelegram that can also fail: fail_sends=N makes the next N sends raise."""

    def __init__(self):
        super().__init__()
        self.fail_sends = 0

    def send(self, chat_id, text, reply_markup=None, reply_to=None):
        if self.fail_sends:
            self.fail_sends -= 1
            raise TelegramError("sendMessage: Bad Gateway")
        return super().send(chat_id, text, reply_markup, reply_to)


@pytest.fixture
def tg():
    return FakeTelegram()

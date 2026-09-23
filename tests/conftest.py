"""Fixtures shared by tests/alerts and tests/workers."""

import pytest

from surgesignal.alerts.telegram import TelegramError
from surgesignal.config import Config
from surgesignal.engine.params import load_params
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


class FakeTelegram:
    """Records every call; send() returns increasing message ids. fail_sends=N fails the next N sends."""

    def __init__(self):
        self.sent, self.edits, self.answers = [], [], []
        self.pending_updates = []
        self.fail_sends = 0
        self._next_id = 100

    def send(self, chat_id, text, reply_markup=None, reply_to=None):
        if self.fail_sends:
            self.fail_sends -= 1
            raise TelegramError("sendMessage: Bad Gateway")
        self._next_id += 1
        self.sent.append({"chat": chat_id, "text": text, "markup": reply_markup, "reply_to": reply_to, "id": self._next_id})
        return self._next_id

    def edit(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append({"chat": chat_id, "id": message_id, "text": text, "markup": reply_markup})

    def updates(self, offset, timeout):
        out, self.pending_updates = self.pending_updates, []
        return out

    def answer_callback(self, callback_id, text=None):
        self.answers.append((callback_id, text))


@pytest.fixture
def tg():
    return FakeTelegram()

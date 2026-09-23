"""Minimal Telegram Bot API client over HTTPS (no SDK): send, edit, long-poll, answer buttons."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

PostFn = Callable[[str, dict[str, Any], float], dict[str, Any]]


class TelegramError(RuntimeError):
    pass


def _post(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:  # Telegram puts the reason in a JSON body
        try:
            return json.loads(exc.read())
        except ValueError:
            raise TelegramError(f"HTTP {exc.code}") from exc


class Telegram:
    def __init__(self, token: str, post: PostFn = _post) -> None:
        self.base = f"https://api.telegram.org/bot{token}/"
        self.post = post

    def call(self, method: str, http_timeout: float = 20, **params: Any) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        resp = self.post(self.base + method, payload, http_timeout)
        if not resp.get("ok"):
            raise TelegramError(f"{method}: {resp.get('description', resp)}")
        return resp["result"]

    def send(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None,
             reply_to: int | None = None) -> int:
        params: dict[str, Any] = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup}
        if reply_to is not None:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return self.call("sendMessage", **params)["message_id"]

    def edit(self, chat_id: int, message_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
        except TelegramError as exc:
            if "message is not modified" not in str(exc):  # same text again is not a failure
                raise

    def updates(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        """Long-poll for new messages and button presses (waits up to `timeout` seconds)."""
        return self.call("getUpdates", http_timeout=timeout + 10, offset=offset, timeout=timeout,
                         allowed_updates=["message", "callback_query"])

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)


def keyboard(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    """Inline keyboard from [(label, callback_data), ...] rows."""
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in rows]}

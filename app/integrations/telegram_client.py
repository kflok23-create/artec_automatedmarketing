"""Telegram — the weekly gate transport. Long polling only; no webhook, no scheduler.

API parameters travel as an explicit dict, NEVER as **kwargs into `_call`: Telegram's own
parameter names (getUpdates takes `timeout` — the long-poll seconds) would otherwise
collide with transport options in `_call`'s signature. The HTTP timeout is a separate,
explicitly-named channel and must always exceed the long-poll window, or the connection is
cut mid-poll.
"""

from __future__ import annotations

import httpx

from app.settings import Settings


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, settings: Settings):
        self._base = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
        self.chat_id = settings.TELEGRAM_CHAT_ID

    def _call(self, method: str, params: dict | None = None, http_timeout: float = 65) -> dict:
        with httpx.Client(timeout=http_timeout) as client:
            resp = client.post(f"{self._base}/{method}", json=params or {})
        body = resp.json()
        if not body.get("ok"):
            # description is Telegram's text; the token lives only in the URL, never echoed.
            raise TelegramError(f"telegram {method} failed: {body.get('description', 'unknown error')}")
        return body["result"]

    def get_me(self) -> dict:
        return self._call("getMe")

    def send_message(self, text: str, buttons: list[list[dict]] | None = None) -> dict:
        params: dict = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        return self._call("sendMessage", params)

    def get_updates(self, offset: int | None = None, timeout_s: int = 25) -> list[dict]:
        # `timeout` here is Telegram's long-poll duration (an API parameter); the HTTP
        # timeout rides separately and exceeds it so the poll is never cut mid-wait.
        params: dict = {"timeout": timeout_s, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", params, http_timeout=timeout_s + 15)

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

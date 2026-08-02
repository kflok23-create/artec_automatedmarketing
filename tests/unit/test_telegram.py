"""Real Telegram client over a mocked wire — doctor's getMe never exercises getUpdates,
which is how a parameter/transport name collision reached the live gate."""

import json

import httpx
import pytest
import respx

from app.integrations.telegram_client import Telegram, TelegramError
from app.settings import Settings

API = "https://api.telegram.org"


def _ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


@respx.mock
def test_get_updates_long_poll_param_reaches_telegram():
    route = respx.post(url__regex=rf"{API}/bot.*/getUpdates").mock(return_value=_ok([]))
    tg = Telegram(Settings())
    assert tg.get_updates(offset=7, timeout_s=25) == []
    body = json.loads(route.calls.last.request.content)
    assert body["timeout"] == 25          # Telegram's long-poll seconds — an API param
    assert body["offset"] == 7
    assert body["allowed_updates"] == ["message", "callback_query"]


@respx.mock
def test_get_updates_http_timeout_exceeds_poll_window():
    route = respx.post(url__regex=rf"{API}/bot.*/getUpdates").mock(return_value=_ok([]))
    Telegram(Settings()).get_updates(timeout_s=25)
    ext = route.calls.last.request.extensions.get("timeout", {})
    # The transport read timeout must outlast the 25 s long poll or it cuts mid-wait.
    assert ext.get("read", 0) >= 40


@respx.mock
def test_send_message_and_answer_callback_shapes():
    send = respx.post(url__regex=rf"{API}/bot.*/sendMessage").mock(return_value=_ok({"message_id": 1}))
    answer = respx.post(url__regex=rf"{API}/bot.*/answerCallbackQuery").mock(return_value=_ok(True))
    tg = Telegram(Settings())
    tg.send_message("hi", buttons=[[{"text": "A", "callback_data": "app:x"}]])
    tg.answer_callback("cb1", "done")
    sent = json.loads(send.calls.last.request.content)
    assert sent["chat_id"] == "42" and sent["reply_markup"]["inline_keyboard"][0][0]["text"] == "A"
    answered = json.loads(answer.calls.last.request.content)
    assert answered == {"callback_query_id": "cb1", "text": "done"}


@respx.mock
def test_api_error_surfaces_description_not_token():
    respx.post(url__regex=rf"{API}/bot.*/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": False, "description": "Unauthorized"})
    )
    with pytest.raises(TelegramError, match="Unauthorized"):
        Telegram(Settings()).get_updates()

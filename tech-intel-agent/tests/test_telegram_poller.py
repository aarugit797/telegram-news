"""
Replaces tests/test_webhook.py, which covered the Twilio signature check
on a route that no longer exists.

What needed covering did not disappear with that route - it moved. The
webhook test protected two things: that untrusted input cannot reach the
agent chain, and that the transport hands the chain the right shape. Long
polling has no signature to verify (the bot token in the URL is the
credential), so the equivalent risks here are that a non-text update
reaches the paid chain, and that offset tracking fails and replays work.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.responder.telegram_poller import _extract, poll_once


def test_extract_returns_chat_id_and_text():
    """The shape process_message expects: chat_id as a STRING, plus text."""
    update = {"update_id": 1, "message": {"text": "hello", "chat": {"id": 6335782585}}}
    assert _extract(update) == ("6335782585", "hello")


def test_extract_normalises_chat_id_to_string():
    """
    Telegram sends chat_id as an integer; channel_user_id is String(100).
    If this ever returned an int, every whitelist lookup would silently
    miss and every user would look un-whitelisted.
    """
    chat_id, _ = _extract({"update_id": 1, "message": {"text": "x", "chat": {"id": 42}}})
    assert isinstance(chat_id, str)


@pytest.mark.parametrize(
    "update",
    [
        {"update_id": 1, "message": {"sticker": {}, "chat": {"id": 1}}},   # no text
        {"update_id": 2, "my_chat_member": {}},                            # not a message
        {"update_id": 3, "message": {"text": "", "chat": {"id": 1}}},      # empty text
        {"update_id": 4, "message": {"text": "hi"}},                       # no chat
    ],
)
def test_extract_rejects_updates_the_chain_cannot_answer(update):
    """
    Non-text updates must not reach the agent chain. Each one that does
    costs four real LLM calls to answer an empty string.
    """
    assert _extract(update) is None


@pytest.mark.asyncio
async def test_offset_advances_past_processed_updates():
    """
    Offset tracking is what stops Telegram redelivering work already done.
    Returning last_update_id + 1 is the acknowledgement.
    """
    client = AsyncMock()
    client.get.return_value.json = lambda: {
        "ok": True,
        "result": [
            {"update_id": 100, "message": {"text": "a", "chat": {"id": 7}}},
            {"update_id": 101, "message": {"text": "b", "chat": {"id": 7}}},
        ],
    }
    with patch("app.responder.telegram_poller.process_message", new_callable=AsyncMock):
        assert await poll_once(client, None) == 102


@pytest.mark.asyncio
async def test_offset_advances_even_when_processing_raises():
    """
    A message that fails processing must NOT be retried forever. It is
    logged and the offset still advances - otherwise one malformed
    message blocks every later one behind it, permanently.
    """
    client = AsyncMock()
    client.get.return_value.json = lambda: {
        "ok": True,
        "result": [{"update_id": 200, "message": {"text": "boom", "chat": {"id": 7}}}],
    }
    failing = AsyncMock(side_effect=RuntimeError("chain blew up"))
    with patch("app.responder.telegram_poller.process_message", failing):
        assert await poll_once(client, None) == 201
    assert failing.await_count == 1


@pytest.mark.asyncio
async def test_offset_unchanged_when_telegram_reports_failure():
    """
    ok=false is an application-level error carried by an HTTP 200. The
    offset must not move, or the updates we never saw are acknowledged
    and lost.
    """
    client = AsyncMock()
    client.get.return_value.json = lambda: {"ok": False, "error_code": 401,
                                            "description": "Unauthorized"}
    assert await poll_once(client, 55) == 55

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

from app.core.config import settings
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
        assert await poll_once(client, None, {}) == 102


@pytest.mark.asyncio
async def test_offset_holds_when_processing_fails():
    """
    THE OFFSET IS AN ACKNOWLEDGEMENT, so it may only move once the reply
    actually went out. It used to advance whenever process_message
    returned, and a failed send returned like any other call - so a reply
    that cost five LLM calls could be discarded while Telegram was told
    the message was handled. Unrecoverable by construction.

    Holding the offset is what makes Telegram redeliver.
    """
    client = AsyncMock()
    client.get.return_value.json = lambda: {
        "ok": True,
        "result": [{"update_id": 200, "message": {"text": "boom", "chat": {"id": 7}}}],
    }
    failing = AsyncMock(side_effect=RuntimeError("send failed"))
    with patch("app.responder.telegram_poller.process_message", failing):
        assert await poll_once(client, None, {}) is None
    assert failing.await_count == 1


@pytest.mark.asyncio
async def test_a_failure_stops_the_rest_of_the_batch():
    """
    The offset is a high-water mark, not a set. Advancing past a later
    update would acknowledge the failed one along with it, so everything
    behind a failure stays unacknowledged too.
    """
    client = AsyncMock()
    client.get.return_value.json = lambda: {
        "ok": True,
        "result": [
            {"update_id": 300, "message": {"text": "boom", "chat": {"id": 7}}},
            {"update_id": 301, "message": {"text": "later", "chat": {"id": 7}}},
        ],
    }
    failing = AsyncMock(side_effect=RuntimeError("send failed"))
    with patch("app.responder.telegram_poller.process_message", failing):
        assert await poll_once(client, None, {}) is None
    # The second update was never attempted.
    assert failing.await_count == 1


@pytest.mark.asyncio
async def test_repeated_failure_is_dead_lettered_rather_than_blocking_forever():
    """
    The ceiling on redelivery. Without it, an update that fails every
    time wedges the bot permanently - the offset never advances, the same
    message is redelivered forever, and every other reader is stuck
    behind it. At the limit it is recorded and skipped, never dropped
    silently.
    """
    client = AsyncMock()
    client.get.return_value.json = lambda: {
        "ok": True,
        "result": [{"update_id": 400, "message": {"text": "poison", "chat": {"id": 7}}}],
    }
    failing = AsyncMock(side_effect=RuntimeError("always fails"))
    attempts = {400: settings.max_update_attempts - 1}
    dead_letter = AsyncMock()

    with patch("app.responder.telegram_poller.process_message", failing),             patch("app.responder.telegram_poller.push_dead_letter", dead_letter),             patch("app.responder.telegram_poller.sentry_sdk.capture_message") as alert:
        assert await poll_once(client, None, attempts) == 401

    assert dead_letter.await_count == 1
    assert dead_letter.await_args[0][0]["update_id"] == 400
    assert alert.call_count == 1


@pytest.mark.asyncio
async def test_attempts_are_cleared_after_a_success():
    """
    A message that failed once and then succeeded must not carry its
    count forward - otherwise an unrelated blip later spends a budget
    already half used.
    """
    client = AsyncMock()
    client.get.return_value.json = lambda: {
        "ok": True,
        "result": [{"update_id": 500, "message": {"text": "fine", "chat": {"id": 7}}}],
    }
    attempts = {500: 1}
    with patch("app.responder.telegram_poller.process_message", new_callable=AsyncMock):
        assert await poll_once(client, None, attempts) == 501
    assert 500 not in attempts


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
    assert await poll_once(client, 55, {}) == 55

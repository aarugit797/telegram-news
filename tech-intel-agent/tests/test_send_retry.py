"""
The send path's retry behaviour.

A reply costs four or five LLM calls to produce, so losing it to a
dropped TCP connection is the most expensive failure in the responder.
One real message was lost exactly that way: the connection was reset
mid-response, the composed reply was discarded, and the reader got
silence.
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.channels.telegram import TelegramChannel
from app.core.config import settings


def _ok_response():
    response = AsyncMock()
    response.json = lambda: {"ok": True, "result": {"message_id": 42}}
    return response


def _client_yielding(*results):
    """An AsyncClient context manager whose post() walks `results`."""
    client = AsyncMock()
    client.post = AsyncMock(side_effect=list(results))
    ctx = AsyncMock()
    ctx.__aenter__.return_value = client
    return ctx, client


@pytest.mark.asyncio
async def test_transient_error_is_retried_and_the_reply_arrives():
    """The failure that lost a real message: connection reset mid-send."""
    ctx, client = _client_yielding(
        httpx.RemoteProtocolError("Not enough data to satisfy transfer length header"),
        _ok_response(),
    )
    channel = TelegramChannel(bot_token="t")

    with patch("httpx.AsyncClient", return_value=ctx), \
            patch("asyncio.sleep", new_callable=AsyncMock):
        message_id = await channel.send_message("7", "hello")

    assert message_id == "42"
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_dns_failure_is_retried():
    """getaddrinfo failure surfaces as ConnectError - the live outage."""
    ctx, client = _client_yielding(
        httpx.ConnectError("[Errno 11001] getaddrinfo failed"),
        httpx.ConnectError("[Errno 11001] getaddrinfo failed"),
        _ok_response(),
    )
    channel = TelegramChannel(bot_token="t")

    with patch("httpx.AsyncClient", return_value=ctx), \
            patch("asyncio.sleep", new_callable=AsyncMock):
        assert await channel.send_message("7", "hello") == "42"
    assert client.post.await_count == 3


@pytest.mark.asyncio
async def test_a_permanently_failing_send_raises_after_the_retry_budget():
    """
    Raising is what lets the poller hold the offset. Returning quietly
    would reproduce the original bug one layer down.
    """
    attempts = settings.send_max_retries + 1
    ctx, client = _client_yielding(*[httpx.ConnectError("down")] * attempts)
    channel = TelegramChannel(bot_token="t")

    with patch("httpx.AsyncClient", return_value=ctx), \
            patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(httpx.ConnectError):
            await channel.send_message("7", "hello")

    assert client.post.await_count == attempts


@pytest.mark.asyncio
async def test_telegram_application_errors_are_not_retried():
    """
    An invalid chat_id or a blocked bot is a verdict, not a hiccup - it
    fails identically every time, so retrying only delays the failure.
    Note Telegram returns these as HTTP 200 with ok=false.
    """
    rejected = AsyncMock()
    rejected.json = lambda: {"ok": False, "error_code": 400,
                             "description": "chat not found"}
    ctx, client = _client_yielding(rejected)
    channel = TelegramChannel(bot_token="t")

    with patch("httpx.AsyncClient", return_value=ctx), \
            patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError, match="chat not found"):
            await channel.send_message("7", "hello")

    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_a_successful_send_makes_exactly_one_request():
    ctx, client = _client_yielding(_ok_response())
    channel = TelegramChannel(bot_token="t")

    with patch("httpx.AsyncClient", return_value=ctx):
        assert await channel.send_message("7", "hello") == "42"
    assert client.post.await_count == 1

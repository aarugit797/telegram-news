"""
Inbound Telegram, via long polling.

WHY POLLING AND NOT WEBHOOKS. A webhook needs a public HTTPS endpoint
with a valid certificate, which during development means ngrok or a
deploy for every change. Long polling needs neither, so the entire
responder chain - whitelist, rate limit, guardrail, intent classifier,
tools, composer - can be exercised end to end on a laptop.

WEBHOOKS ARE THE RIGHT CHOICE ONCE DEPLOYED. They remove the idle
request loop, deliver with lower latency, and scale without a dedicated
polling process. Telegram supports setWebhook with a `secret_token`,
which it then sends back in the X-Telegram-Bot-Api-Secret-Token header on
every request - that header is what makes the endpoint verifiable, and it
is the direct equivalent of the Twilio signature check already in
webhook.py. Switching over means calling setWebhook and adding a route
that checks that header; this module then simply stops being run.

The responder chain itself is channel-agnostic and needs no changes for
either transport: this module's only job is to turn an update into the
(sender_id, text) pair _process_message already takes.
"""
import asyncio

import httpx

from app.core.config import settings
from app.core.logging_config import get_logger
from app.responder.webhook import _process_message

logger = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# Seconds Telegram holds an empty getUpdates open before replying. A long
# hold is what makes polling cheap: one request covers 30 quiet seconds
# instead of thirty requests finding nothing.
LONG_POLL_TIMEOUT_SECONDS = 30

# Must exceed LONG_POLL_TIMEOUT_SECONDS, or the client aborts the request
# Telegram is legitimately still holding open.
HTTP_TIMEOUT_SECONDS = LONG_POLL_TIMEOUT_SECONDS + 15


def _api(method: str) -> str:
    return f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/{method}"


def _extract(update: dict) -> tuple[str, str] | None:
    """
    Reduces an update to (chat_id, text), or None if it is not something
    this system can answer.

    Non-text updates - stickers, photos, edited messages, channel posts,
    members joining - are skipped rather than fed to the agent chain,
    which expects text and would otherwise spend LLM calls on an empty
    string.
    """
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None

    text = message.get("text")
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if not text or chat_id is None:
        return None

    # chat_id is an integer in the API and a string everywhere in our
    # schema (channel_user_id is String(100)), so it is normalised once,
    # here, rather than at each comparison.
    return str(chat_id), text


async def poll_once(client: httpx.AsyncClient, offset: int | None) -> int | None:
    """
    One getUpdates cycle. Returns the next offset to use.

    OFFSET TRACKING is what stops an update being processed twice.
    Telegram keeps redelivering an update until it is acknowledged, and
    the acknowledgement is implicit: requesting offset = last_update_id+1
    tells Telegram everything below that is handled. Without it, every
    poll would replay the same message and re-run the whole paid agent
    chain on it.

    The offset is advanced only AFTER a message is processed, so a crash
    mid-processing means Telegram redelivers it - at-least-once, matching
    the delivery property the rest of the pipeline is built on.
    """
    params: dict = {"timeout": LONG_POLL_TIMEOUT_SECONDS}
    if offset is not None:
        params["offset"] = offset

    response = await client.get(_api("getUpdates"), params=params)
    payload = response.json()

    if not payload.get("ok"):
        logger.error(
            "Telegram getUpdates failed",
            extra={"extra_fields": {
                "error_code": payload.get("error_code"),
                "description": payload.get("description"),
            }},
        )
        return offset

    for update in payload.get("result", []):
        update_id = update["update_id"]
        parsed = _extract(update)

        if parsed is None:
            logger.info(
                "Skipping non-text Telegram update",
                extra={"extra_fields": {"update_id": update_id}},
            )
        else:
            chat_id, text = parsed
            logger.info(
                "Telegram message received",
                extra={"extra_fields": {
                    # chat_id, never the message body - the same reason
                    # webhook.py stopped logging message text.
                    "chat_id": chat_id,
                    "update_id": update_id,
                    "text_length": len(text),
                }},
            )
            try:
                # The SAME function the Twilio webhook calls, unchanged.
                # Everything channel-specific ends at this line.
                await _process_message(chat_id, text)
            except Exception as e:
                logger.error(
                    "Telegram message processing failed",
                    extra={"extra_fields": {
                        "chat_id": chat_id, "update_id": update_id,
                        "error": str(e), "error_type": type(e).__name__,
                    }},
                )

        offset = update_id + 1

    return offset


async def run_telegram_poller() -> None:
    """
    Polls forever. Entrypoint for the responder process when
    active_channel is telegram.
    """
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set - the poller cannot start")

    logger.info("Telegram poller starting")
    offset: int | None = None

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        while True:
            try:
                offset = await poll_once(client, offset)
            except Exception as e:
                # A network blip must not kill the loop. Backing off
                # briefly avoids hammering Telegram while it is unhappy.
                logger.error(
                    "Telegram poll cycle failed",
                    extra={"extra_fields": {"error": str(e), "error_type": type(e).__name__}},
                )
                await asyncio.sleep(5)

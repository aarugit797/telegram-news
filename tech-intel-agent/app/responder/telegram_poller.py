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
every request - that header is what makes the endpoint verifiable.
Switching over means calling setWebhook and adding a route that checks
it; responder/message_handler.py is already transport-agnostic, so that
route would call the same process_message this module does, and this
module simply stops being run.

The responder chain needs no changes for either transport: this module's
only job is to turn an update into the (chat_id, text) pair
process_message already takes.
"""
import asyncio
import random

import httpx
import sentry_sdk

from app.core.config import settings
from app.core.logging_config import get_logger
from app.queues.redis_client import (
    acquire_poller_lock,
    push_dead_letter,
    release_poller_lock,
)
from app.responder.message_handler import process_message

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


async def poll_once(
    client: httpx.AsyncClient, offset: int | None, attempts: dict[int, int]
) -> int | None:
    """
    One getUpdates cycle. Returns the next offset to use.

    OFFSET TRACKING is what stops an update being processed twice.
    Telegram keeps redelivering an update until it is acknowledged, and
    the acknowledgement is implicit: requesting offset = last_update_id+1
    tells Telegram everything below that is handled.

    THE OFFSET ADVANCES ONLY WHEN THE REPLY WAS ACTUALLY DELIVERED, which
    is a correction rather than a refinement. It used to advance after
    process_message RETURNED - and a send that failed still returned,
    because the failure happened inside the processed path instead of
    crashing out of it. So the at-least-once guarantee held for crashes
    and quietly did not hold for the far more common case: the reply was
    composed, the send died on a dropped connection, the answer was
    discarded, and Telegram was told the message had been handled. It was
    unrecoverable by construction.

    THE COST, STATED RATHER THAN DISCOVERED: not advancing means Telegram
    redelivers, and the whole chain re-runs for that message - guardrail,
    classifier, tool, composer, four or five LLM calls, paid again. A
    duplicate reply is worth more than a silently dropped one, so this is
    the right trade, but it is a trade and not a free win.

    A FAILURE STOPS THE BATCH. The remaining updates in this response are
    left unacknowledged too, because advancing past them would skip the
    one that failed - the offset is a high-water mark, not a set.

    `attempts` bounds the re-run. Without a ceiling, an update that fails
    every time wedges the bot permanently: the offset never advances, so
    the same message is redelivered forever and every other reader is
    stuck behind it.
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
                    # message_handler stopped logging message text.
                    "chat_id": chat_id,
                    "update_id": update_id,
                    "text_length": len(text),
                }},
            )
            attempts[update_id] = attempts.get(update_id, 0) + 1
            try:
                # Everything Telegram-specific ends at this line;
                # process_message knows nothing about the transport.
                await process_message(chat_id, text)
            except Exception as e:
                logger.error(
                    "Telegram message processing failed",
                    extra={"extra_fields": {
                        "chat_id": chat_id, "update_id": update_id,
                        "attempt": attempts[update_id],
                        "of": settings.max_update_attempts,
                        "error": str(e), "error_type": type(e).__name__,
                    }},
                )

                if attempts[update_id] < settings.max_update_attempts:
                    # Leave the offset where it is so Telegram redelivers
                    # this update, and stop here rather than processing
                    # the ones behind it.
                    return offset

                # Out of attempts. Give up on THIS message so it cannot
                # block the queue behind it, but record it somewhere a
                # human can find it - dropping it silently is the exact
                # failure this whole function is being changed to remove.
                await push_dead_letter({
                    "component": "telegram_poller",
                    "update_id": update_id,
                    "chat_id": chat_id,
                    "attempts": attempts[update_id],
                    "error": str(e),
                    "error_type": type(e).__name__,
                })
                sentry_sdk.capture_message(
                    "Telegram update abandoned after repeated failures",
                    level="error",
                )
                logger.error(
                    "Telegram update abandoned - dead-lettered",
                    extra={"extra_fields": {
                        "chat_id": chat_id, "update_id": update_id,
                        "attempts": attempts[update_id],
                    }},
                )

            attempts.pop(update_id, None)

        offset = update_id + 1

    return offset


async def run_telegram_poller() -> None:
    """
    Polls forever. Entrypoint for the responder process when
    active_channel is telegram.

    ONLY ONE POLLER MAY RUN AT A TIME, and that is enforced here rather
    than assumed. The offset is a local variable, so it protects against
    re-processing WITHIN one process and can do nothing about a second
    process holding its own copy. Two pollers therefore each fetch the
    same update and each run the full chain on it.

    That is not hypothetical. Two instances ran against one bot and every
    message produced two inbound rows and two replies - and because the
    chain is non-deterministic, the two replies could disagree: one pass
    failed to resolve an item number and sent "I'm not sure which repo
    you mean", while the other resolved it correctly and answered. The
    reader saw a clarifying question immediately followed by the answer
    to the question it had just asked.

    The lock has a TTL and is refreshed each cycle, so a killed poller
    frees it within a minute rather than locking the bot out forever.
    """
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set - the poller cannot start")

    if not await acquire_poller_lock():
        # Deliberately a hard stop, not a warning-and-continue. A second
        # poller that keeps running IS the bug; the only safe thing it
        # can do is not poll.
        raise RuntimeError(
            "Another Telegram poller holds the lock - refusing to start a second one. "
            "Stop the running poller first, or wait for its lock to expire."
        )

    logger.info("Telegram poller starting")
    offset: int | None = None
    attempts: dict[int, int] = {}
    consecutive_failures = 0
    alerted = False

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            while True:
                try:
                    offset = await poll_once(client, offset, attempts)

                    if consecutive_failures:
                        logger.info(
                            "Telegram polling recovered",
                            extra={"extra_fields": {
                                "after_failures": consecutive_failures,
                            }},
                        )
                    consecutive_failures = 0
                    alerted = False

                except Exception as e:
                    # A network blip must not kill the loop.
                    consecutive_failures += 1
                    logger.error(
                        "Telegram poll cycle failed",
                        extra={"extra_fields": {
                            "error": str(e), "error_type": type(e).__name__,
                            "consecutive_failures": consecutive_failures,
                        }},
                    )

                    # A LOG LINE IS NOT AN ALERT. At this level logging
                    # only produces a Sentry breadcrumb, which nobody sees
                    # unless something else raises - and during an outage
                    # nothing else does. Three and a half hours of a dead
                    # bot passed unnoticed exactly this way.
                    #
                    # Fired once per outage, not once per cycle: repeating
                    # it every few seconds would bury the signal it exists
                    # to raise.
                    if consecutive_failures >= settings.poller_failure_alert_threshold \
                            and not alerted:
                        sentry_sdk.capture_message(
                            f"Telegram poller failing: {consecutive_failures} "
                            f"consecutive cycles ({type(e).__name__})",
                            level="error",
                        )
                        alerted = True

                    # Backoff grows with the outage instead of hammering a
                    # dead network every 5 seconds, and is jittered so
                    # several responders do not retry in lockstep.
                    ceiling = min(2 ** consecutive_failures,
                                  settings.poller_backoff_max_seconds)
                    await asyncio.sleep(random.uniform(0, ceiling))

                finally:
                    # REFRESHED ON EVERY CYCLE, including failed ones.
                    # This used to sit on the success path, so an outage
                    # stopped the refresh entirely and the lock expired
                    # after 60 seconds - leaving a live, network-blocked
                    # poller holding nothing and a second one free to
                    # start. Two pollers is the bug the lock exists to
                    # prevent, and a poller that cannot reach the network
                    # is still the owner: a replacement would fare no
                    # better and would double-process once the network
                    # returned.
                    try:
                        await acquire_poller_lock(refresh=True)
                    except Exception as lock_error:
                        logger.warning(
                            "Poller lock refresh failed",
                            extra={"extra_fields": {
                                "error": str(lock_error),
                                "error_type": type(lock_error).__name__,
                            }},
                        )
    finally:
        await release_poller_lock()

import asyncio
import random

import httpx

from app.core.channels.base import MessageChannel
from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# Telegram's documented limit is roughly one message per second to the
# SAME chat (bursts to a group are limited separately). batch_sender
# already spaced a batch's messages 2s apart so they arrive as separate
# bubbles rather than one burst, which independently satisfies this - so
# the spacing lives in the sender, one layer up, rather than being
# duplicated here. This constant records the constraint the sender's
# spacing has to respect.
MIN_SECONDS_BETWEEN_MESSAGES_TO_ONE_CHAT = 1.0

# Telegram rejects messages over 4096 characters with a 400. LLM-written
# text is normally far shorter, but a composer that ignores its length
# instruction would otherwise lose the whole message rather than most of
# it.
MAX_MESSAGE_CHARS = 4096

# TRANSIENT: the request never reached Telegram, or the response never
# came back intact. Nothing is known to have happened, so retrying is
# safe and is very likely to work - these are the shapes a dropped
# network takes.
#
# RemoteProtocolError covers the truncated-response case seen live
# ("Not enough data to satisfy transfer length header"), and
# ConnectionResetError arrives as a bare OSError from the socket layer
# rather than wrapped by httpx, so it is listed separately.
_TRANSIENT_SEND_ERRORS = (
    httpx.ConnectError,        # DNS failure, refused connection
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    ConnectionResetError,
    ConnectionAbortedError,
)

# NOT retried, deliberately: a Telegram application error is a verdict,
# not a hiccup. An invalid chat_id, a blocked bot or a malformed
# parse_mode will fail identically on every attempt, so retrying only
# delays the failure and burns the reader's time.


def _send_backoff_seconds(attempt: int) -> float:
    """
    Exponential backoff with full jitter, matching llm_client.

    The jitter matters for the same reason it does there: a digest fans
    out to every active user, so an outage would otherwise have all of
    them retrying in lockstep and re-colliding on each wave.
    """
    ceiling = min(
        settings.send_backoff_base_seconds * (2 ** attempt),
        settings.send_backoff_max_seconds,
    )
    return random.uniform(0, ceiling)


class TelegramChannel(MessageChannel):
    """
    Telegram Bot API over plain httpx.

    No SDK dependency on purpose. Sending is a single form POST to
    /sendMessage with chat_id and text; pulling in python-telegram-bot to
    do that would add an async framework, a job queue and a handler
    system we would not use, and every dependency added here has to be
    kept compatible with the httpx and pydantic pins google-genai already
    constrains.
    """

    name = "telegram"

    def __init__(self, bot_token: str | None = None) -> None:
        token = bot_token if bot_token is not None else settings.telegram_bot_token
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set - required when active_channel='telegram'"
            )
        # Held here and nowhere else. The token is a full bot credential,
        # so it must never reach a log field or an error message: it is
        # embedded in the URL path, which is why _api() exists rather
        # than formatting urls at each call site.
        self._token = token

    def _api(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{self._token}/{method}"

    async def send_message(
        self, channel_user_id: str, text: str, rich: bool = False
    ) -> str:
        """
        Returns Telegram's message_id as a string.

        Errors are raised, not swallowed - the sender worker's per-user
        try/except decides what a failure means for the batch, and it can
        only do that if the failure actually reaches it.
        """
        payload: dict = {
            "chat_id": channel_user_id,
            "text": text[:MAX_MESSAGE_CHARS],
            "disable_web_page_preview": True,
        }

        # parse_mode is OPT-IN and off by default.
        #
        # Model-written text regularly contains underscores, asterisks and
        # angle brackets. Asking Telegram to parse that as markup turns a
        # stray character into a 400 that drops a real message, so the
        # responder's replies - raw model output - stay plain.
        #
        # The digest is different: it is assembled in
        # processor/message_composer.py with every model-written fragment
        # escaped before it goes near a tag. That escaping is what makes
        # markup safe there and nowhere else, which is why this is a
        # per-caller decision rather than a global setting.
        if rich:
            payload["parse_mode"] = "HTML"

        # RETRIED, because the cost of losing this is asymmetric. A reply
        # has already cost four or five LLM calls by the time it reaches
        # here, and the reader's alternative to a retry is silence. One
        # real message was lost this way: the connection was reset
        # mid-response, the reply was discarded, and nothing was ever
        # delivered.
        response = None
        last_error: Exception | None = None

        for attempt in range(settings.send_max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(self._api("sendMessage"), json=payload)
                last_error = None
                break
            except _TRANSIENT_SEND_ERRORS as e:
                last_error = e
                if attempt == settings.send_max_retries:
                    break
                delay = _send_backoff_seconds(attempt)
                logger.warning(
                    "Telegram send failed, retrying",
                    extra={"extra_fields": {
                        # chat_id, never the text - same rule as everywhere
                        # else that touches a message.
                        "chat_id": channel_user_id,
                        "attempt": attempt + 1,
                        "of": settings.send_max_retries,
                        "sleep_seconds": round(delay, 2),
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }},
                )
                await asyncio.sleep(delay)

        if last_error is not None:
            # Every attempt failed. Raise rather than returning quietly,
            # so the caller can decide what it means - for the poller that
            # means NOT advancing the offset, which is what makes Telegram
            # redeliver instead of the message vanishing.
            raise last_error

        payload = response.json()
        if not payload.get("ok"):
            # Telegram returns 200 with ok=false for application-level
            # failures, so status_code alone is not enough to detect them.
            raise RuntimeError(
                f"telegram sendMessage failed: "
                f"{payload.get('error_code')} {payload.get('description')}"
            )

        return str(payload["result"]["message_id"])

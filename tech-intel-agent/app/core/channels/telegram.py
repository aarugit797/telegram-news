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

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self._api("sendMessage"), json=payload)

        payload = response.json()
        if not payload.get("ok"):
            # Telegram returns 200 with ok=false for application-level
            # failures, so status_code alone is not enough to detect them.
            raise RuntimeError(
                f"telegram sendMessage failed: "
                f"{payload.get('error_code')} {payload.get('description')}"
            )

        return str(payload["result"]["message_id"])

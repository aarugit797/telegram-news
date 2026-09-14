"""
Channel selection.

The active channel is chosen once from config rather than passed through
every call, because a single deployment talks to exactly one messaging
service - a user is reachable on Telegram or on WhatsApp, and the
pipeline has no reason to decide per message.
"""
from app.core.channels.base import MessageChannel
from app.core.channels.telegram import TelegramChannel
from app.core.channels.whatsapp import WhatsAppChannel
from app.core.config import settings

_CHANNELS = {
    "telegram": TelegramChannel,
    "whatsapp": WhatsAppChannel,
}

_channel: MessageChannel | None = None


def get_channel() -> MessageChannel:
    """
    The configured channel, built once on first use.

    Lazy for the same reason twilio_client is: constructing a channel can
    require credentials or a running event loop, and doing that at import
    time makes the whole scheduler and responder unimportable when the
    inactive channel happens to be misconfigured.
    """
    global _channel
    if _channel is None:
        name = settings.active_channel.strip().lower()
        if name not in _CHANNELS:
            raise RuntimeError(
                f"unknown active_channel {name!r} - expected one of {sorted(_CHANNELS)}"
            )
        _channel = _CHANNELS[name]()
    return _channel


def reset_channel() -> None:
    """Drops the cached channel. For tests and for switching at runtime."""
    global _channel
    _channel = None


async def send_message(channel_user_id: str, text: str) -> str:
    """Module-level convenience so call sites need not know about the factory."""
    return await get_channel().send_message(channel_user_id, text)

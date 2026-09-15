"""
Channel selection.

The active channel is chosen once from config rather than passed through
every call, because a single deployment talks to exactly one messaging
service, and the pipeline has no reason to decide per message.
"""
from app.core.channels.base import MessageChannel
from app.core.channels.telegram import TelegramChannel
from app.core.config import settings

# One entry today. The registry and the active_channel setting are kept
# rather than collapsed into "just import TelegramChannel", because this
# seam is what made replacing Twilio a config change instead of a rewrite
# - it earned its keep once already, and removing it now only to add it
# back for the next channel would be pure churn.
_CHANNELS = {
    "telegram": TelegramChannel,
}

_channel: MessageChannel | None = None


def get_channel() -> MessageChannel:
    """
    The configured channel, built once on first use.

    Lazy on purpose: constructing a channel requires credentials, and
    doing that at import time makes the whole scheduler and responder
    unimportable when a token is missing - including for the pipeline
    processes that never send a message at all.
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


async def send_message(channel_user_id: str, text: str, rich: bool = False) -> str:
    """Module-level convenience so call sites need not know about the factory."""
    return await get_channel().send_message(channel_user_id, text, rich=rich)

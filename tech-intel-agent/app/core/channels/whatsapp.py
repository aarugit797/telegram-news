from app.core.channels.base import MessageChannel
from app.core.twilio_client import send_whatsapp_message


class WhatsAppChannel(MessageChannel):
    """
    Twilio WhatsApp, behind the channel interface.

    core/twilio_client.py is deliberately NOT deleted or inlined here.
    Its contents are load-bearing knowledge that took real effort to get
    right - the lazy client construction that avoids "no running event
    loop" at import, and the AsyncTwilioHttpClient / *_async pairing that
    keeps Twilio calls from blocking the loop. This class is a thin
    adapter over that, so re-enabling WhatsApp is a config change rather
    than a rewrite.

    Known limitation, and the reason this is not the default: on a Twilio
    trial account sends fail with 21654 "ContentSid Required", because
    business-initiated WhatsApp messages need pre-approved templates.
    That is incompatible with LLM-generated text, which is different
    every time and cannot be pre-approved. Making this work needs a paid
    account and an approved WABA, not a code change.
    """

    name = "whatsapp"

    async def send_message(self, channel_user_id: str, text: str) -> str:
        # channel_user_id is an E.164 phone number on this channel.
        return await send_whatsapp_message(channel_user_id, text)

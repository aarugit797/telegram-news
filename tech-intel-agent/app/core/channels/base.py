"""
The messaging-channel interface.

Same shape as the LLM provider adapter in core/llm_client.py, and for the
same reason: one internal interface, provider-specific translation behind
it, so the places that send a message never learn which service carries
it.

HISTORY, because the shape looks over-built for a single implementation.
This started as Twilio WhatsApp. That turned out to be unusable: sending
LLM-written text failed with 21654 "ContentSid Required", because
business-initiated WhatsApp messages require pre-approved templates -
fundamentally incompatible with text written fresh for every message.
Telegram has no approval process and no template restriction.

The swap was a config change rather than a rewrite BECAUSE this interface
existed. Twilio has since been removed entirely (Telegram is the product,
not a stopgap), but the seam stays: it is forty lines, it has already
proved its worth once, and the next channel - Discord, Slack, email -
plugs in here.
"""
from abc import ABC, abstractmethod


class MessageChannel(ABC):
    """
    One method, because sending a message is genuinely all the pipeline
    asks of a channel. Receiving is deliberately NOT part of this
    interface - inbound differs too much between a Twilio webhook and
    Telegram long polling to share one abstraction honestly, and forcing
    them together would produce a shape that fits neither.
    """

    name: str

    @abstractmethod
    async def send_message(
        self, channel_user_id: str, text: str, rich: bool = False
    ) -> str:
        """
        Delivers one message and returns the provider's message id.

        `channel_user_id` is deliberately not called a phone number: it is
        a Telegram chat_id on one channel and an E.164 number on the
        other. Naming it for either would bake one channel's assumption
        into the interface meant to hide it.

        `rich` opts into channel-native markup. OFF by default, and only
        safe for a caller that ASSEMBLED the message itself and escaped
        every untrusted fragment in it. Raw model output must never be
        sent rich - one stray angle bracket becomes a parse error that
        drops the whole message.
        """
        ...

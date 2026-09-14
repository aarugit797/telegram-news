"""
The messaging-channel interface.

Same shape as the LLM provider adapter in core/llm_client.py, and for the
same reason: one internal interface, provider-specific translation behind
it, so the twelve places that send a message never learn which service
actually carries it.

WHY THIS EXISTS. Twilio's WhatsApp sandbox turned out to be unusable on a
trial account - verified empirically, not assumed: sending arbitrary text
fails with 21654 "ContentSid Required", the parallel SMS attempt says it
plainly ("Trial accounts can only use predefined SMS templates"), and the
Content API needed to create a template is itself blocked on trial. That
is a closed loop. Business-initiated WhatsApp messages need pre-approved
templates, which are fundamentally incompatible with text an LLM writes
fresh for each batch.

Telegram has no approval process and no template restriction. The
WhatsApp implementation is KEPT, not deleted, so it can be switched back
on with one config value if a WhatsApp Business Account is obtained.
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
    async def send_message(self, channel_user_id: str, text: str) -> str:
        """
        Delivers one message and returns the provider's message id.

        `channel_user_id` is deliberately not called a phone number: it is
        a Telegram chat_id on one channel and an E.164 number on the
        other. Naming it for either would bake one channel's assumption
        into the interface meant to hide it.
        """
        ...

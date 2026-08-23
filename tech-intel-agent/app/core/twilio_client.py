"""
STUB - to be built.

WHAT: Low-level shared wrapper around the Twilio API for sending a
single WhatsApp message.

WHY: Both sender/twilio_sender.py (proactive batch notifications)
and the responder (reply messages) need to send WhatsApp messages.
Rather than duplicate the Twilio SDK call in two places, both call
through this one function.

INPUT: destination WhatsApp number, message text.

OUTPUT: Twilio message SID and delivery status.

CONNECTS TO: Used by app/sender/twilio_sender.py and by the
responder's reply-sending logic (inside webhook.py's response flow).
"""

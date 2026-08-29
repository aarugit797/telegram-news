from twilio.http.async_http_client import AsyncTwilioHttpClient
from twilio.rest import Client

from app.core.config import settings

# Twilio's SDK is synchronous by default. AsyncTwilioHttpClient (built
# on aiohttp) plus the *_async method variants (create_async, not
# create) give a genuinely non-blocking call - consistent with the
# rest of our async codebase. Using the plain sync client here would
# block our event loop for the duration of every Twilio API call,
# undoing the whole point of building this system async.
_http_client = AsyncTwilioHttpClient()
_client = Client(settings.twilio_account_sid, settings.twilio_auth_token, http_client=_http_client)


async def send_whatsapp_message(to_number: str, body: str) -> str:
    """
    Low-level shared Twilio wrapper - both sender/twilio_sender.py
    (proactive notifications) and the responder's reply-sending logic
    call through this one function, instead of each independently
    wrapping the Twilio SDK.
    """
    message = await _client.messages.create_async(
        from_=settings.twilio_whatsapp_number,
        to=f"whatsapp:{to_number}",
        body=body,
    )
    return message.sid

from app.db.repository_conversation import is_user_whitelisted, get_or_create_user
from app.db.session_conversation import get_conversation_session


async def check_whitelist(whatsapp_number: str) -> bool:
    """
    First check on any incoming message, before any LLM call. Uses
    is_user_whitelisted (not get_or_create_user) as the actual gate -
    an unknown number is simply not whitelisted, full stop, rather
    than being silently created and allowed through.
    """
    async with get_conversation_session() as session:
        return await is_user_whitelisted(session, whatsapp_number)


async def ensure_user_record(whatsapp_number: str):
    """
    Called only AFTER check_whitelist passes - ensures a User row
    exists (first message from a pre-approved number still needs a
    row created) so later steps have a real user_id to attach
    messages, costs, and summaries to.
    """
    async with get_conversation_session() as session:
        return await get_or_create_user(session, whatsapp_number)

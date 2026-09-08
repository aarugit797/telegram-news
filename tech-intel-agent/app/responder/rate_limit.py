from app.queues.redis_client import increment_rate_limit

DAILY_MESSAGE_LIMIT = 50


async def check_and_increment_rate_limit(user_id: str) -> bool:
    """
    Called after the whitelist check passes. Increments today's
    count FIRST, then checks the result against the limit - this
    means the message that pushes a user over the limit is itself
    counted (so the limit is a hard "50 total today", not "50 before
    you're warned"). Returns True if the user is still under the cap.
    """
    count = await increment_rate_limit(user_id)
    return count <= DAILY_MESSAGE_LIMIT

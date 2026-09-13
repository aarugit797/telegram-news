from datetime import date

from app.core.config import settings
from app.core.logging_config import get_logger
from app.db.repository_conversation import get_daily_usage, increment_daily_usage
from app.db.session_conversation import get_conversation_session

logger = get_logger(__name__)


async def is_under_cost_limit(user_id) -> bool:
    """
    Checked before allowing the conversational agent to run at all - if a
    user has already hit today's allowance from earlier messages, this
    message gets a fixed response with ZERO further LLM calls made.

    The budget is QUOTA, not dollars. On free tiers there is no per-call
    price to cap; what is scarce is the ~3,000 requests/day shared across
    every credential, of which the responder alone can take ~500. One
    user sending fifty messages is spending an allowance that belongs to
    everybody.

    BOTH limits are checked because they bind at different times. A user
    sending many short messages hits the request cap first; a user in a
    long conversation, where every turn carries a growing history, hits
    the token cap while their request count still looks modest.
    """
    async with get_conversation_session() as session:
        calls, tokens = await get_daily_usage(session, user_id, date.today())

    if calls >= settings.user_daily_request_limit:
        logger.info(
            "User over daily request limit",
            extra={"extra_fields": {
                "user_id": str(user_id), "calls": calls,
                "limit": settings.user_daily_request_limit, "limit_type": "requests",
            }},
        )
        return False

    if tokens >= settings.user_daily_token_limit:
        logger.info(
            "User over daily token limit",
            extra={"extra_fields": {
                "user_id": str(user_id), "tokens": tokens,
                "limit": settings.user_daily_token_limit, "limit_type": "tokens",
            }},
        )
        return False

    return True


async def record_llm_usage(user_id, calls: int, tokens: int) -> None:
    """
    Persists one message's LLM usage. Replaces record_llm_cost, whose
    `cost` parameter is meaningless on a free tier - there is no dollar
    price to convert tokens into, and pretending otherwise was part of
    why that function was never wired up.

    Called ONCE per inbound message, at the end of _process_message, with
    the totals contextvar-accumulated across the whole chain (guardrail,
    intent classifier, history summarizer, tool, composer) rather than
    once per individual LLM call. One write instead of five, and the
    numbers cannot drift from what the chain actually spent.

    A count of zero is not written - a message rejected by the guardrail
    before any tool runs still made LLM calls, but one rejected by the
    whitelist or rate limiter made none, and an empty row would only add
    noise.
    """
    if calls <= 0:
        return

    async with get_conversation_session() as session:
        await increment_daily_usage(session, user_id, date.today(), calls, tokens)

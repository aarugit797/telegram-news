from datetime import date

from app.db.repository_conversation import get_daily_cost, increment_daily_cost
from app.db.session_conversation import get_conversation_session

DAILY_COST_LIMIT_USD = 0.50


async def is_under_cost_limit(user_id) -> bool:
    """
    Checked before allowing the conversational agent to run at all -
    if a user has already hit today's cap from earlier messages, this
    message gets a fixed response with zero further LLM calls made.
    """
    async with get_conversation_session() as session:
        current_cost = await get_daily_cost(session, user_id, date.today())
    return current_cost < DAILY_COST_LIMIT_USD


async def record_llm_cost(user_id, cost: float, tokens: int) -> None:
    """
    Called after each LLM call in the responder flow completes, using
    the token counts LLMResult already returns (see core/llm_client.py) -
    actual per-model $ pricing to convert tokens into `cost` is the
    caller's responsibility, kept out of this file since pricing
    varies by which model a given call used.
    """
    async with get_conversation_session() as session:
        await increment_daily_cost(session, user_id, date.today(), cost, tokens)

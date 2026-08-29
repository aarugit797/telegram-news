import uuid
from datetime import datetime, date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models_conversation import User, Message, Summary, DailyCost


async def is_user_whitelisted(session: AsyncSession, whatsapp_number: str) -> bool:
    """
    Used by responder/whitelist.py - the very first check on any
    incoming message, before any LLM call is made.
    """
    result = await session.execute(
        select(User).where(User.whatsapp_number == whatsapp_number)
    )
    user = result.scalar_one_or_none()
    return user is not None and user.is_whitelisted


async def get_active_users(session: AsyncSession) -> list[User]:
    """
    Used by sender/twilio_sender.py to know who to fan a composed
    batch's messages out to - every whitelisted, active user.
    """
    result = await session.execute(
        select(User).where(User.is_whitelisted == True, User.is_active == True)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_or_create_user(session: AsyncSession, whatsapp_number: str) -> User:
    """
    Used right after the whitelist check passes. Whitelisted numbers
    may still need their first User row created on their very first
    message (depending on how the whitelist itself is seeded) - this
    guarantees we always have a real user.id to attach messages to.
    """
    result = await session.execute(
        select(User).where(User.whatsapp_number == whatsapp_number)
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(whatsapp_number=whatsapp_number, is_whitelisted=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def get_daily_message_count(session: AsyncSession, user_id: uuid.UUID) -> int:
    """
    Used by responder/rate_limit.py to check against the 50/day cap.
    Reads the counter directly off the User row rather than counting
    Message rows every time, which would get slower as history grows.
    """
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    return user.daily_message_count if user else 0


async def increment_daily_message_count(session: AsyncSession, user_id: uuid.UUID) -> None:
    """
    Called once per inbound message. Note: resetting this back to 0
    at the start of each new day is handled by a separate scheduled
    job, not by this function.
    """
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user:
        user.daily_message_count += 1
        user.last_active_at = datetime.utcnow()
        await session.commit()


async def save_message(session: AsyncSession, message_data: dict) -> Message:
    """
    Used for BOTH inbound (direction="inbound") and outbound
    (direction="outbound") messages - every single message in or out
    passes through this one function, so the messages table is a
    complete log.
    """
    message = Message(**message_data)
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


async def get_recent_messages(
    session: AsyncSession, user_id: uuid.UUID, limit: int = 5
) -> list[Message]:
    """
    Used by guardrail.py (needs last 5, to catch multi-turn injection
    attempts) and token_budget.py (needs recent raw history before
    deciding if older messages need summarizing).
    Returns newest-first.
    """
    result = await session.execute(
        select(Message)
        .where(Message.user_id == user_id, Message.is_deleted == False)  # noqa: E712
        .order_by(Message.timestamp.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def save_summary(session: AsyncSession, summary_data: dict) -> Summary:
    """
    Used by token_budget.py when it compresses older messages into
    one paragraph, so future calls don't need to re-send the full
    raw history every time.
    """
    summary = Summary(**summary_data)
    session.add(summary)
    await session.commit()
    await session.refresh(summary)
    return summary


async def get_latest_summary(session: AsyncSession, user_id: uuid.UUID) -> Summary | None:
    """
    Used by token_budget.py to check if a compressed summary already
    exists before deciding whether a new one needs to be created.
    """
    result = await session.execute(
        select(Summary)
        .where(Summary.user_id == user_id)
        .order_by(Summary.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_daily_cost(session: AsyncSession, user_id: uuid.UUID, day: date) -> float:
    """
    Used by responder/cost_tracker.py before allowing an LLM call -
    checks today's running total against the $0.50 cap.
    """
    result = await session.execute(
        select(DailyCost).where(DailyCost.user_id == user_id, DailyCost.date == day)
    )
    record = result.scalar_one_or_none()
    return record.estimated_cost_usd if record else 0.0


async def increment_daily_cost(
    session: AsyncSession, user_id: uuid.UUID, day: date, cost: float, tokens: int
) -> None:
    """
    Called after every LLM call in the responder flow. Creates
    today's row if it doesn't exist yet, otherwise adds to it.
    """
    result = await session.execute(
        select(DailyCost).where(DailyCost.user_id == user_id, DailyCost.date == day)
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = DailyCost(
            user_id=user_id, date=day, llm_calls=1, total_tokens=tokens, estimated_cost_usd=cost
        )
        session.add(record)
    else:
        record.llm_calls += 1
        record.total_tokens += tokens
        record.estimated_cost_usd += cost
    await session.commit()

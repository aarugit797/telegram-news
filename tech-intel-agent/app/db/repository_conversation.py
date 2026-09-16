import uuid
from datetime import datetime, date

from app.core.time import utcnow

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models_conversation import User, Message, Summary, DailyCost


async def is_user_whitelisted(session: AsyncSession, channel: str, channel_user_id: str) -> bool:
    """
    Whitelist check, scoped to the channel. Both parts are required:
    the same id string on a different service is a different person.
    """
    result = await session.execute(
        select(User).where(
            User.channel == channel,
            User.channel_user_id == channel_user_id,
        )
    )
    user = result.scalar_one_or_none()
    return bool(user and user.is_whitelisted and user.is_active)


async def get_active_users(session: AsyncSession, channel: str) -> list[User]:
    """
    Used by sender/batch_sender.py to know who to fan a composed batch's
    messages out to - every whitelisted, active user ON THIS CHANNEL.

    The channel filter is not optional. channel_user_id means different
    things per channel: a Telegram chat_id and an E.164 phone number are
    not interchangeable, so handing a WhatsApp user's number to the
    Telegram sender produces a guaranteed failure, a dead-letter entry
    and a "partial" batch status - for a user who was never reachable on
    the active channel in the first place.

    Returning users the active channel cannot reach would make every
    batch look partially broken while nothing was actually wrong.
    """
    result = await session.execute(
        select(User).where(
            User.channel == channel,
            User.is_whitelisted == True,  # noqa: E712
            User.is_active == True,       # noqa: E712
        )
    )
    return list(result.scalars().all())


async def get_or_create_user(session: AsyncSession, channel: str, channel_user_id: str) -> User:
    """
    Fetches this channel's user, creating the row on first contact.

    is_whitelisted defaults True here, matching the previous behaviour -
    a user who reached this point already passed check_whitelist, and
    whitelisting is enforced there rather than by this row's default.
    """
    result = await session.execute(
        select(User).where(
            User.channel == channel,
            User.channel_user_id == channel_user_id,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(channel=channel, channel_user_id=channel_user_id, is_whitelisted=True)
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
        user.last_active_at = utcnow()
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


def _between(user_id: uuid.UUID, after: datetime | None, before: datetime | None):
    """
    Shared filter for the rolling-summary reads below.

    `after` is the previous summary's covers_to, so the window starts
    where the last summary stopped - which is what makes the summary
    accumulate instead of sliding. `before` is the oldest message being
    kept raw, so the summary never duplicates the tail the caller is
    about to send verbatim.

    Both bounds are exclusive. covers_to IS the timestamp of a message
    already folded in, so `>` rather than `>=` is what stops that message
    being summarised twice.
    """
    conditions = [Message.user_id == user_id, Message.is_deleted == False]  # noqa: E712
    if after is not None:
        conditions.append(Message.timestamp > after)
    if before is not None:
        conditions.append(Message.timestamp < before)
    return conditions


async def count_messages_between(
    session: AsyncSession, user_id: uuid.UUID,
    after: datetime | None = None, before: datetime | None = None,
) -> int:
    """
    How many messages are waiting to be summarised.

    A COUNT rather than len() of a fetch: this runs on EVERY inbound
    message to decide whether the summarizer is worth calling, and all
    the caller needs is a number. Fetching the rows to count them would
    make the common answer - "not yet" - the expensive one.
    """
    result = await session.execute(
        select(func.count()).select_from(Message).where(*_between(user_id, after, before))
    )
    return int(result.scalar() or 0)


async def get_messages_between(
    session: AsyncSession, user_id: uuid.UUID,
    after: datetime | None = None, before: datetime | None = None,
    limit: int = 200,
) -> list[Message]:
    """
    The messages to fold into the next summary, OLDEST FIRST - summaries
    read as narrative, so the model should see them in the order they
    happened.

    The limit is a safety rail, not a window. Regeneration is triggered
    every SUMMARY_REFRESH_EVERY messages, so the backlog is normally a
    fraction of this; 200 only matters if summarisation has been broken
    or disabled for a long stretch, and there it prevents one enormous
    prompt rather than silently dropping history the way a fixed window
    would. When it does bite, the oldest messages are the ones kept,
    since covers_to then advances and the rest are folded in next time.
    """
    result = await session.execute(
        select(Message)
        .where(*_between(user_id, after, before))
        .order_by(Message.timestamp.asc())
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


async def get_daily_usage(session: AsyncSession, user_id: uuid.UUID, day: date) -> tuple[int, int]:
    """
    Today's (llm_calls, total_tokens) for this user. Used by
    responder/cost_tracker.py before the conversational agent runs.

    Returns requests AND tokens because the budget is quota, not
    dollars, and the two bind at different times: a handful of
    long-context messages can exhaust a token allowance while barely
    touching a request count.
    """
    result = await session.execute(
        select(DailyCost).where(DailyCost.user_id == user_id, DailyCost.date == day)
    )
    record = result.scalar_one_or_none()
    if record is None:
        return 0, 0
    return record.llm_calls, record.total_tokens


async def increment_daily_usage(
    session: AsyncSession, user_id: uuid.UUID, day: date, calls: int, tokens: int
) -> None:
    """
    Adds one message's usage to today's row, creating it if absent.

    ATOMIC, via INSERT ... ON CONFLICT DO UPDATE. The previous version
    did read-modify-write in Python with no lock, which lost updates:
    two of a user's messages processing concurrently would both read the
    same starting value and both write their own total, so one
    increment vanished - and the user could exceed a cap that looked
    correct in the database.

    The conflict target is the uq_daily_costs_user_date constraint. That
    constraint also closes the second half of the old race: without it,
    the two concurrent INSERTs both succeeded and left two rows for one
    (user, date), after which get_daily_usage's scalar_one_or_none()
    raised MultipleResultsFound on EVERY subsequent read for that user
    that day - turning a lost increment into a hard failure.

    The addition happens inside Postgres (llm_calls + excluded.llm_calls)
    rather than in Python, so concurrent callers serialise on the row
    lock instead of racing.
    """
    statement = pg_insert(DailyCost).values(
        user_id=user_id,
        date=day,
        llm_calls=calls,
        total_tokens=tokens,
        estimated_cost_usd=0.0,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_daily_costs_user_date",
        set_={
            "llm_calls": DailyCost.llm_calls + statement.excluded.llm_calls,
            "total_tokens": DailyCost.total_tokens + statement.excluded.total_tokens,
        },
    )
    await session.execute(statement)
    await session.commit()

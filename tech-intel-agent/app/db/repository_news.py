import uuid
from datetime import datetime

from app.core.time import utcnow

from sqlalchemy import select, update, delete, exists
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models_news import Signal, Batch, RejectedSignal

# How many unsent signals one batching run will consider. Named rather
# than inlined so it is discoverable, and kept a parameter so a caller
# or a test can override it without editing this file.
DEFAULT_UNSENT_LIMIT = 40


async def insert_signal(session: AsyncSession, signal_data: dict) -> Signal:
    """
    Used by all 5 agents after a signal passes both stages of the
    hybrid filter. signal_data is a dict matching the Signal model's
    columns (source, title, url, full_content, summary, the 3 scores,
    composite_score, filter_justification, embedding).
    """
    signal = Signal(**signal_data)
    session.add(signal)          # stages the insert - not yet sent to Postgres
    await session.commit()       # actually sends it and waits for confirmation
    await session.refresh(signal)  # reloads any DB-generated fields (like id, created_at)
    return signal


async def signal_exists_by_url(session: AsyncSession, url: str) -> bool:
    """
    Used by every agent as the FIRST check, before rules_check or any
    LLM call - a repo/paper/post that stays trending/visible across
    multiple scheduled runs (e.g. GitHub agent runs every 2 hours; a
    repo can stay trending for days) would otherwise get re-scored
    and potentially re-fetched on every single run, wasting LLM calls
    and API calls on something already evaluated. Checking is_deleted
    == False only, deliberately not filtering on age - a signal that
    already exists and hasn't been cleaned up yet is still a
    duplicate, regardless of how long ago it was written.
    """
    result = await session.execute(
        select(Signal).where(Signal.url == url, Signal.is_deleted == False)  # noqa: E712
    )
    return result.scalar_one_or_none() is not None


async def insert_rejected_signal(session: AsyncSession, rejected_data: dict) -> None:
    """
    Records a url the LLM filter stage rejected, so the next run can
    skip it without paying for a second identical judgment. Called by
    all 5 agents, but ONLY on stage_reached == "rejected_by_llm" - a
    rules-stage rejection never reached the LLM and so costs nothing
    to repeat.

    Returns None rather than the row: nothing downstream needs the
    object back, and not refreshing saves a round trip on what is the
    most frequent write in the pipeline.
    """
    session.add(RejectedSignal(**rejected_data))
    await session.commit()


async def url_was_rejected(session: AsyncSession, url: str) -> bool:
    """
    The read side of the rejection cache. Runs on EVERY item of every
    run, immediately after signal_exists_by_url, so it is deliberately
    an EXISTS subquery: Postgres stops at the first matching index
    entry and returns a boolean, instead of materialising a full row
    that would be discarded. At this call frequency that difference is
    the whole point of the function.

    Filters is_deleted == False to match the cleanup policy - once an
    entry has been soft-deleted the url becomes eligible for scoring
    again, which is what lets a re-judged item back in after 30 days.
    """
    result = await session.execute(
        select(
            exists().where(
                RejectedSignal.url == url,
                RejectedSignal.is_deleted == False,  # noqa: E712
            )
        )
    )
    return bool(result.scalar())


async def get_unsent_signals(
    session: AsyncSession, limit: int = DEFAULT_UNSENT_LIMIT
) -> list[Signal]:
    """
    Used by the batching agent every 30 minutes. Returns unsent,
    un-soft-deleted signals, HIGHEST composite_score first, capped.

    The cap exists because this feeds two unbounded consumers. Signals
    that are not chosen for a batch stay unsent, so a backlog only
    grows: deduplicate_signals then builds one prompt listing every row
    and the batching agent classified urgency once per cluster. An
    overnight backlog turns a 30-minute job into something that cannot
    finish inside its own interval, and a backlog is exactly the state
    that produces one.

    Ordering by composite_score DESC rather than by age is deliberate
    and changes nothing downstream: assemble_batch already picks the
    top MAX_SIGNALS_PER_BATCH by composite_score, so capping on the same
    key yields the identical batch it would have chosen from the full
    set - it just stops loading the whole table to get there.

    The tradeoff is that a large sustained backlog will starve its
    lowest-scoring rows, which cleanup_job eventually soft-deletes. For
    a "best three things right now" digest that is the correct outcome:
    a week-old mediocre signal has no value once it is a week old.
    """
    result = await session.execute(
        select(Signal)
        .where(
            Signal.is_sent == False,   # noqa: E712 - SQLAlchemy requires == not `is False` here
            Signal.is_deleted == False,
        )
        .order_by(Signal.composite_score.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def mark_signals_as_sent(
    session: AsyncSession, signal_ids: list[uuid.UUID], batch_id: uuid.UUID
) -> None:
    """
    Used by the batching agent right after a batch is successfully
    composed. Flips is_sent=True and attaches the batch_id on every
    signal that went into this batch, in one single query rather
    than one query per signal.
    """
    await session.execute(
        update(Signal)
        .where(Signal.id.in_(signal_ids))
        .values(is_sent=True, sent_at=utcnow(), batch_id=batch_id)
    )
    await session.commit()


async def create_batch(
    session: AsyncSession, signal_ids: list[uuid.UUID], user_count: int
) -> Batch:
    """
    Used by batch_assembly.py to create the Batch row itself, with
    its idempotent UUID, BEFORE marking the individual signals as
    sent - this ordering matters, since mark_signals_as_sent() needs
    a real batch_id to attach to each signal, which only exists once
    this function has run.
    """
    batch = Batch(signal_ids=signal_ids, user_count=user_count)
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    return batch


async def get_batch_by_id(session: AsyncSession, batch_id: uuid.UUID) -> Batch | None:
    """
    Used by the Notification History Tool - given a batch_id (which
    it gets from the Conversation DB's messages table, or from the
    user's most recent batch), retrieves exactly which signals were
    included in that specific notification.
    """
    result = await session.execute(select(Batch).where(Batch.id == batch_id))
    return result.scalar_one_or_none()


async def get_most_recent_batch(session: AsyncSession) -> Batch | None:
    """
    Used by the Notification History Tool. Since v1 sends an
    identical batch to every active user (no personalization - see
    project scope), "the most recent batch sent to this user" is
    simply the most recent successfully delivered batch, period -
    there is no per-user batch link to look up.
    """
    result = await session.execute(
        select(Batch)
        .where(Batch.delivery_status.in_(["delivered", "partial"]))
        .order_by(Batch.sent_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_signals_by_ids(session: AsyncSession, signal_ids: list[uuid.UUID]) -> list[Signal]:
    """Used by the Notification History Tool to fetch the actual Signal rows for a batch's signal_ids."""
    result = await session.execute(select(Signal).where(Signal.id.in_(signal_ids)))
    return list(result.scalars().all())


async def search_signals_by_embedding(
    session: AsyncSession, query_embedding: list[float], limit: int = 3
) -> list[Signal]:
    """
    Used by the News DB Tool for RAG search. Given a vector (the
    user's question, already converted to an embedding), finds the
    `limit` most semantically similar signals using pgvector's
    cosine distance operator.

    `.cosine_distance()` returns a SMALLER number for MORE similar
    vectors (0 = identical direction, 2 = completely opposite) - so
    we order ascending and take the top `limit` results.
    """
    result = await session.execute(
        select(Signal)
        .where(Signal.is_deleted == False)  # noqa: E712
        .order_by(Signal.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    return list(result.scalars().all())


async def soft_delete_old_signals(session: AsyncSession, older_than: datetime) -> int:
    """
    Used by the weekly cleanup job. Sets is_deleted=True on any
    signal created before `older_than` and not already soft-deleted.
    Soft delete rather than removing the row outright - keeps data
    recoverable and stats history intact. Returns rows affected.
    """
    result = await session.execute(
        update(Signal)
        .where(Signal.created_at < older_than, Signal.is_deleted == False)  # noqa: E712
        .values(is_deleted=True)
    )
    await session.commit()
    return result.rowcount


async def hard_delete_old_signals(session: AsyncSession, older_than: datetime) -> int:
    """
    Used by the weekly cleanup job - permanently removes signals that
    are ALREADY soft-deleted and older than the hard-delete cutoff.
    Never deletes something in one step that hasn't already passed
    through the soft-delete stage first.
    """
    result = await session.execute(
        delete(Signal).where(Signal.created_at < older_than, Signal.is_deleted == True)  # noqa: E712
    )
    await session.commit()
    return result.rowcount


async def soft_delete_old_rejected_signals(session: AsyncSession, older_than: datetime) -> int:
    """
    Weekly cleanup, mirroring soft_delete_old_signals. Expiring the
    cache matters as much as filling it: a repo rejected a year ago
    under an older prompt or threshold should eventually be reconsidered
    rather than excluded forever by a stale judgment.
    """
    result = await session.execute(
        update(RejectedSignal)
        .where(RejectedSignal.rejected_at < older_than, RejectedSignal.is_deleted == False)  # noqa: E712
        .values(is_deleted=True)
    )
    await session.commit()
    return result.rowcount


async def hard_delete_old_rejected_signals(session: AsyncSession, older_than: datetime) -> int:
    """
    Permanently removes rejection cache entries that are ALREADY
    soft-deleted, mirroring hard_delete_old_signals - never deleting in
    one step something that has not passed through soft delete first.
    """
    result = await session.execute(
        delete(RejectedSignal).where(
            RejectedSignal.rejected_at < older_than, RejectedSignal.is_deleted == True  # noqa: E712
        )
    )
    await session.commit()
    return result.rowcount


async def update_batch_delivery(
    session: AsyncSession, batch_id: uuid.UUID, user_count: int, delivery_status: str
) -> None:
    """
    Used by sender/twilio_sender.py AFTER actually attempting
    delivery - fills in how many users were reached and whether it
    succeeded. Deliberately not set at batch_assembly time, since
    neither is known until delivery has genuinely been attempted.
    """
    result = await session.execute(select(Batch).where(Batch.id == batch_id))
    batch = result.scalar_one_or_none()
    if batch:
        batch.user_count = user_count
        batch.delivery_status = delivery_status
        batch.sent_at = utcnow()
        await session.commit()

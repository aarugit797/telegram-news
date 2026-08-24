import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models_news import Signal, Batch


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


async def get_unsent_signals(session: AsyncSession) -> list[Signal]:
    """
    Used by the batching agent every 30 minutes. Returns every signal
    that hasn't been sent yet and hasn't been soft-deleted.
    """
    result = await session.execute(
        select(Signal).where(
            Signal.is_sent == False,   # noqa: E712 - SQLAlchemy requires == not `is False` here
            Signal.is_deleted == False,
        )
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
        .values(is_sent=True, sent_at=datetime.utcnow(), batch_id=batch_id)
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
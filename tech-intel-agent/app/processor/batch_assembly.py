import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository_news import create_batch, mark_signals_as_sent

MAX_SIGNALS_PER_BATCH = 3


async def assemble_batch(session: AsyncSession, clusters: list[list]):
    """
    Picks each cluster's representative (highest composite_score),
    ranks all representatives, takes the top MAX_SIGNALS_PER_BATCH,
    creates the Batch row with its idempotent UUID, and marks EVERY
    signal in an included cluster as sent - not just the
    representative - so duplicates never get re-considered in a
    future run.

    Deliberately does NOT set user_count here - that's only known
    after sender/twilio_sender.py actually attempts delivery.
    """
    representatives = [max(cluster, key=lambda s: s.composite_score) for cluster in clusters]
    representatives.sort(key=lambda s: s.composite_score, reverse=True)
    top_representatives = representatives[:MAX_SIGNALS_PER_BATCH]
    top_rep_ids = {r.id for r in top_representatives}

    all_signal_ids = []
    for cluster in clusters:
        rep = max(cluster, key=lambda s: s.composite_score)
        if rep.id in top_rep_ids:
            all_signal_ids.extend(str(s.id) for s in cluster)

    batch = await create_batch(session, signal_ids=all_signal_ids, user_count=0)
    await mark_signals_as_sent(session, [uuid.UUID(sid) for sid in all_signal_ids], batch.id)

    return batch, top_representatives

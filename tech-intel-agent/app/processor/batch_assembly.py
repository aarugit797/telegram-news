import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.repository_news import create_batch

# Kept as a module constant for anything still importing it, but the
# digest passes its own limit. It was 3 when delivery was a continuous
# stream of small batches; a digest carries up to
# settings.max_signals_per_digest.
MAX_SIGNALS_PER_BATCH = 3


async def assemble_batch(
    session: AsyncSession, clusters: list[list], limit: int | None = None
):
    """
    Picks each cluster's representative (highest composite_score),
    ranks all representatives, takes the top `limit` (defaulting to
    settings.max_signals_per_digest), and creates the Batch row with its
    idempotent UUID.

    Deliberately does NOT mark signals as sent any more. It used to, and
    that was a data-loss bug: marking happened here, BEFORE
    compose_messages ran, so any failure in composition (an LLM timeout,
    a 429, malformed output) left those signals flagged is_sent=True
    with nothing ever delivered. get_unsent_signals would never return
    them again, and no user would ever see them.

    Marking is now the caller's last step, after composition and the
    delivery push have both succeeded - see batching_agent.py. Splitting
    the two responsibilities is what lets the caller control that
    ordering.

    Returns (batch, top_representatives, signal_uuids) - the third
    element being every signal the caller must eventually mark, not just
    the representatives, so duplicates in an included cluster are not
    re-considered on a future run.

    Deliberately does NOT set user_count here - that's only known
    after sender/batch_sender.py actually attempts delivery.
    """
    max_items = limit if limit is not None else settings.max_signals_per_digest
    representatives = [max(cluster, key=lambda s: s.composite_score) for cluster in clusters]
    representatives.sort(key=lambda s: s.composite_score, reverse=True)
    top_representatives = representatives[:max_items]
    top_rep_ids = {r.id for r in top_representatives}

    all_signal_ids = []
    for cluster in clusters:
        rep = max(cluster, key=lambda s: s.composite_score)
        if rep.id in top_rep_ids:
            all_signal_ids.extend(str(s.id) for s in cluster)

    batch = await create_batch(session, signal_ids=all_signal_ids, user_count=0)
    signal_uuids = [uuid.UUID(sid) for sid in all_signal_ids]

    return batch, top_representatives, signal_uuids

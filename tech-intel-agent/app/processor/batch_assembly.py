import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.repository_news import create_batch

# Kept as a module constant for anything still importing it, but the
# digest passes its own limit. It was 3 when delivery was a continuous
# stream of small batches; a digest carries up to
# settings.max_signals_per_digest.
MAX_SIGNALS_PER_BATCH = 3


def _select_with_source_caps(representatives: list, max_items: int) -> list:
    """
    Chooses the digest's items under per-source maximums.

    Selection used to be top-N by composite_score alone, which is
    source-blind: one real digest came out as 5 GitHub repos, 1
    newsletter and 1 blog. The scores were not wrong - GitHub simply had
    a good day - but a digest of five repos is a different product from a
    digest of the day's tech news.

        a. group candidates by source, ranked within each by score
        b. take up to that source's cap from each
        c. if still short of max_items, fill from the highest-scoring
           unselected candidates REGARDLESS of source
        d. rank the final set by score

    THE CAPS ARE MAXIMUMS, NOT RESERVATIONS, and step (c) can push a
    source past its own cap. That is deliberate. If no paper cleared the
    filter today, the alternative to letting GitHub take a third slot is
    shipping a four-item digest with a hole where arxiv would have been -
    and a short digest reads like the product is broken, where a slightly
    skewed one just reads like a quiet day for papers. A full digest
    matters more than strict source balance.

    Input is assumed already sorted by composite_score descending, which
    is what makes the per-source grouping stable without re-sorting.
    """
    caps = settings.digest_source_caps_map()

    selected, overflow = [], []
    per_source: dict[str, int] = {}

    for rep in representatives:
        allowed = caps.get(rep.source, 0)
        if per_source.get(rep.source, 0) < allowed:
            per_source[rep.source] = per_source.get(rep.source, 0) + 1
            selected.append(rep)
        else:
            # Not discarded - still the best remaining candidate if the
            # capped pass leaves the digest short.
            overflow.append(rep)

        if len(selected) == max_items:
            break

    if len(selected) < max_items:
        selected.extend(overflow[:max_items - len(selected)])

    selected.sort(key=lambda s: s.composite_score, reverse=True)
    return selected[:max_items]


async def assemble_batch(
    session: AsyncSession, clusters: list[list], limit: int | None = None
):
    """
    Picks each cluster's representative (highest composite_score),
    selects up to `limit` of them under the per-source caps (see
    _select_with_source_caps), and creates the Batch row with its
    idempotent UUID.

    EVERYTHING NOT SELECTED STAYS UNSENT. Only the signals in the
    returned list are ever marked, so the rest are still `is_sent =
    false` and get picked up by the next digest, competing on score
    against whatever arrived in between. That needs no counter and no
    schema change - it falls out of get_unsent_signals filtering on the
    flag the caller has not set yet.

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
    top_representatives = _select_with_source_caps(representatives, max_items)
    top_rep_ids = {r.id for r in top_representatives}

    all_signal_ids = []
    for cluster in clusters:
        rep = max(cluster, key=lambda s: s.composite_score)
        if rep.id in top_rep_ids:
            all_signal_ids.extend(str(s.id) for s in cluster)

    batch = await create_batch(session, signal_ids=all_signal_ids, user_count=0)
    signal_uuids = [uuid.UUID(sid) for sid in all_signal_ids]

    return batch, top_representatives, signal_uuids

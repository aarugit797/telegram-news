import uuid
from datetime import timedelta

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.time import utcnow
from app.db.repository_news import (
    create_batch,
    get_unsent_signals,
    get_unsent_signals_since,
    mark_signals_as_sent,
)
from app.db.session_news import get_news_session
from app.processor.batch_assembly import assemble_batch
from app.processor.dedup import deduplicate_signals
from app.processor.message_composer import compose_breaking, compose_digest, order_for_digest
from app.processor.urgency_classifier import classify_urgency
from app.queues.redis_client import push_dead_letter
from app.sender.delivery_queue import push_batch

logger = get_logger(__name__)

# The source whose content can be worth interrupting for. Only AI lab
# blogs announce model releases and capability changes; a trending repo
# or a paper is never urgent enough to break someone's afternoon, so
# nothing else is polled through the day or checked here.
BREAKING_SOURCE = "blogs"


async def run_digest() -> None:
    """
    Composes and queues one digest. Scheduled twice a day.

    WHY DIGESTS. Five agents producing continuously against a
    3-signal-per-batch cap meant most approved signals never reached
    anyone - they sat unsent until cleanup soft-deleted them at 30 days,
    after we had already paid to filter, score and embed them. Two
    digests of up to 8 clears realistic inflow.

    POSTGRES IS THE SOURCE OF TRUTH FOR UNSENT SIGNALS, and this is a
    durability decision rather than a convenience one. A signal that
    reaches this point has been fetched, filtered against the rules,
    scored by an LLM and embedded - it is expensive and it is not
    reproducible, because the trending list that produced it has already
    moved on. Redis is not durable across a restart, so anything held
    only there can vanish silently and nobody would know a signal had
    been dropped. `is_sent = false` in Postgres survives a crash, and a
    digest that runs late still finds everything waiting for it.

    The agents used to ALSO push each approved signal id onto a Redis
    list, which nothing ever read. That push is gone; this is the
    reasoning it used to imply.

    ORDERING IS THE CORRECTNESS PROPERTY. The database flag is the LAST
    thing that happens:

        1. create the Batch row   (first - marking needs a batch_id)
        2. compose the digest     (the LLM call that can fail)
        3. push to Redis          (delivery is now guaranteed to be tried)
        4. mark signals as sent   (only once 2 and 3 both succeeded)

    A failure in step 2 leaves every signal unsent, so the next digest
    picks them up. ACCEPTED TRADEOFF: between 3 and 4 a crash means the
    next run delivers them twice. At-least-once beats at-most-once - a
    duplicate digest is an annoyance, a silently dropped one means the
    product failed and nobody knows.
    """
    try:
        async with get_news_session() as session:
            unsent = await get_unsent_signals(session)

            if not unsent:
                logger.info("Digest: nothing unsent, skipping this run")
                return

            clusters = await deduplicate_signals(unsent)

            batch, top_signals, signal_ids = await assemble_batch(
                session, clusters, limit=settings.max_signals_per_digest
            )

            # Grouped and ranked BEFORE the batch's signal_ids are read
            # back, because the digest numbers items in exactly this order
            # and the notification-history tool resolves "number 3"
            # against it. The two must not diverge.
            ordered = order_for_digest(top_signals)
            ordered_ids = [s.id for s in ordered]

            # THE ORDER IS THE CONTRACT, so it has to be the order that is
            # STORED. This line used to be missing: ordered_ids was
            # computed, logged, and thrown away, while the row kept the
            # list assemble_batch built - which is cluster order, and
            # which also contains the duplicate cluster members that were
            # never displayed. So the stored sequence matched neither the
            # numbering nor the length of what the reader saw, and "the
            # second point" resolved to whatever happened to sit second
            # in that list. In one real digest the reader's item 1 was
            # stored fourth.
            #
            # The fuller list stays local, because marking-as-sent has
            # the opposite requirement: it must cover every clustered
            # duplicate so none is re-considered next run. One field
            # cannot serve both, and the row's job is the numbering.
            batch.signal_ids = ordered_ids
            await session.commit()

            parts = await compose_digest(ordered)

            for part in parts:
                await push_batch(str(batch.id), [part])

            await mark_signals_as_sent(session, signal_ids, batch.id)

            logger.info(
                "Digest run complete",
                extra={"extra_fields": {
                    "unsent_considered": len(unsent),
                    "clusters": len(clusters),
                    "batch_id": str(batch.id),
                    "items_in_digest": len(ordered),
                    "message_parts": len(parts),
                    "sources": sorted({s.source for s in ordered}),
                    "signals_marked_sent": len(signal_ids),
                    "item_order": [str(i) for i in ordered_ids],
                }},
            )

    except Exception as e:
        logger.error(
            "Digest run failed",
            extra={"extra_fields": {"error": str(e), "error_type": type(e).__name__}},
        )
        await push_dead_letter({
            "agent": "digest", "error": str(e), "error_type": type(e).__name__,
        })


async def run_breaking_check() -> None:
    """
    Sends anything genuinely urgent immediately, instead of holding it for
    the next digest. Scheduled every few minutes.

    ZERO LLM CALLS WHEN NOTHING IS NEW. The window query runs first and
    returns immediately if no new blog signal has appeared, so the common
    case - which is almost every run - costs one indexed SELECT.

    WHY A TIME WINDOW rather than a flag. There is no "urgency checked"
    column, so the window IS the bookkeeping: each run looks only at
    signals created since roughly the last one. A signal judged STANDARD
    falls out of the window and waits for the digest rather than being
    re-classified every 30 minutes forever. The window is deliberately
    wider than the interval so a late run cannot skip the gap.

    THE BOUND MATTERS. Urgency classification was once run unbounded,
    once per cluster, before the batch was trimmed - and that was a
    livelock: enough clusters to exhaust the quota meant the run died
    before anything was sent, the signals stayed unsent, and the next run
    made the same calls and died the same way, forever. Capping it is
    what makes this safe to run every few minutes.

    An earlier fix moved classification AFTER the trim to 3, which was
    safe only while nothing consumed the result - a BREAKING signal
    ranked 4th was simply never classified. That is not safe now, so this
    check looks at candidates directly rather than at the digest's
    survivors.
    """
    try:
        cutoff = utcnow() - timedelta(minutes=settings.breaking_lookback_minutes)

        async with get_news_session() as session:
            candidates = await get_unsent_signals_since(
                session, cutoff, source=BREAKING_SOURCE
            )

            if not candidates:
                # The zero-LLM-call path, and the one taken almost every run.
                logger.info(
                    "Breaking check: no new candidates",
                    extra={"extra_fields": {"source": BREAKING_SOURCE, "llm_calls": 0}},
                )
                return

            checked = candidates[:settings.max_urgency_checks_per_run]
            breaking = []
            for signal in checked:
                if await classify_urgency(signal) == "BREAKING":
                    breaking.append(signal)

            logger.info(
                "Breaking check complete",
                extra={"extra_fields": {
                    "candidates": len(candidates),
                    "classified": len(checked),
                    "capped": len(candidates) > len(checked),
                    "breaking": len(breaking),
                }},
            )

            for signal in breaking:
                # One batch row per breaking signal - it is delivered on
                # its own, so it is its own batch. Same ordering rule as
                # the digest: the DB flag is written last.
                batch = await create_batch(session, signal_ids=[str(signal.id)], user_count=0)
                message = await compose_breaking(signal)
                await push_batch(str(batch.id), [message])
                await mark_signals_as_sent(session, [signal.id], batch.id)

                logger.info(
                    "Breaking signal sent immediately",
                    extra={"extra_fields": {
                        "batch_id": str(batch.id),
                        "signal_id": str(signal.id),
                        "title": signal.title,
                    }},
                )

    except Exception as e:
        logger.error(
            "Breaking check failed",
            extra={"extra_fields": {"error": str(e), "error_type": type(e).__name__}},
        )
        await push_dead_letter({
            "agent": "breaking_check", "error": str(e), "error_type": type(e).__name__,
        })

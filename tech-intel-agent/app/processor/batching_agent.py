from app.core.logging_config import get_logger
from app.db.repository_news import get_unsent_signals, mark_signals_as_sent
from app.db.session_news import get_news_session
from app.processor.dedup import deduplicate_signals
from app.processor.urgency_classifier import classify_urgency
from app.processor.batch_assembly import assemble_batch
from app.processor.message_composer import compose_messages
from app.queues.redis_client import push_dead_letter
from app.sender.delivery_queue import push_batch

logger = get_logger(__name__)


async def run_batching_agent() -> None:
    """
    Triggered every 30 minutes by agents/scheduler.py.

    Reads unsent signals directly from the News DB (get_unsent_signals)
    rather than draining the Redis signal queue by ID - a deliberate
    correctness choice: Postgres is durable, while a Redis list can
    silently lose queued items across a restart unless persistence is
    explicitly configured. Agents still call push_signal() - kept as
    a lightweight "something new exists" marker for a possible future
    immediate-wake optimization - but it is NOT relied on here as the
    actual source of truth for what needs processing.

    If nothing is unsent, exits immediately - zero LLM cost for a
    quiet 30-minute window.

    ORDERING IS THE CORRECTNESS PROPERTY HERE. The database flag is the
    LAST thing that happens:

        1. create the Batch row   (first - marking needs a batch_id)
        2. compose messages       (the LLM call that can fail)
        3. push to Redis          (delivery is now guaranteed to be tried)
        4. mark signals as sent   (only once 2 and 3 both succeeded)

    Previously step 4 happened inside step 1, so a failure in step 2 -
    an LLM timeout, a 429, malformed output - permanently flagged those
    signals as sent with nothing delivered. They would never be returned
    by get_unsent_signals again.

    ACCEPTED TRADEOFF: a narrow window remains between step 3 and step 4.
    If the Redis push succeeds and the DB write then fails, the next run
    sees those signals still unsent and delivers them a second time.
    That is deliberate. This is at-least-once delivery, chosen over
    at-most-once, because a duplicate WhatsApp message is a mild
    annoyance a user can shrug off, while silently losing a signal means
    the product quietly failed and nobody finds out. Closing the window
    completely needs a distributed transaction across Redis and
    Postgres, which is disproportionate machinery for a 30-minute
    batching job.
    """
    try:
        async with get_news_session() as session:
            unsent_signals = await get_unsent_signals(session)

            if not unsent_signals:
                logger.info("Batching agent: nothing unsent, skipping this run")
                return

            clusters = await deduplicate_signals(unsent_signals)

            # 1. Batch row first - mark_signals_as_sent needs a real
            #    batch_id to attach, which only exists once this has run.
            batch, top_signals, signal_ids = await assemble_batch(session, clusters)

            # Urgency is classified AFTER assemble_batch and only for the
            # signals that actually made the batch - at most
            # MAX_SIGNALS_PER_BATCH of them.
            #
            # NOTE - true "bypass the 30-minute window" immediate sending
            # is still NOT implemented. That would need agents (or a
            # separate lightweight trigger) to call the batching agent
            # out-of-cycle the moment something BREAKING is found. The
            # result here goes only into the completion log. Moving the
            # call after the trim does narrow what it observes: a
            # BREAKING signal that does not make the top three is no
            # longer classified at all. That costs nothing today, since
            # nothing reads the classification - but whoever implements
            # immediate sending must move this back BEFORE the trim and
            # bound it some other way, or reintroduce the livelock.
            #
            # It used to run once per CLUSTER, before the trim, and was
            # therefore unbounded in the number of LLM calls it made. On
            # a backlog that is self-sustaining: enough clusters to
            # exhaust the quota means the run dies, the signals stay
            # unsent, the backlog is unchanged, and the next run 30
            # minutes later makes exactly the same calls and dies in the
            # same place. Every run after that burns a full day's quota
            # producing nothing, and no amount of waiting clears it.
            #
            # Classifying only the survivors caps this at 3 calls per run
            # regardless of backlog size, so the run always reaches the
            # send that drains it.
            urgencies = {}
            for signal in top_signals:
                urgencies[str(signal.id)] = await classify_urgency(signal)

            # 2. The failure-prone step. If this raises, the signals below
            #    are still unsent and the next run picks them up.
            messages = await compose_messages(top_signals)

            # 3. Hand off for delivery.
            await push_batch(str(batch.id), messages)

            # 4. Only now is it true that these signals were sent.
            await mark_signals_as_sent(session, signal_ids, batch.id)

            logger.info(
                "Batching agent run complete",
                extra={"extra_fields": {
                    "unsent_signals": len(unsent_signals),
                    "clusters": len(clusters),
                    "batch_id": str(batch.id),
                    "messages_composed": len(messages),
                    "signals_marked_sent": len(signal_ids),
                    "urgencies": urgencies,
                }},
            )

    except Exception as e:
        # A failure here used to be entirely silent: this loop had no
        # handler, so nothing reached the dead letter queue and nothing
        # was logged. The signals are safe either way now - they stay
        # unsent and the next run retries them - but the failure itself
        # still needs to be visible, or a provider outage looks
        # identical to a quiet half-hour.
        logger.error(
            "Batching agent run failed",
            extra={"extra_fields": {"error": str(e), "error_type": type(e).__name__}},
        )
        await push_dead_letter({
            "agent": "batching",
            "error": str(e),
            "error_type": type(e).__name__,
        })

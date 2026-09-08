from app.core.logging_config import get_logger
from app.db.repository_news import get_unsent_signals
from app.db.session_news import get_news_session
from app.processor.dedup import deduplicate_signals
from app.processor.urgency_classifier import classify_urgency
from app.processor.batch_assembly import assemble_batch
from app.processor.message_composer import compose_messages
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
    """
    async with get_news_session() as session:
        unsent_signals = await get_unsent_signals(session)

        if not unsent_signals:
            logger.info("Batching agent: nothing unsent, skipping this run")
            return

        clusters = await deduplicate_signals(unsent_signals)

        # Urgency is classified and logged per cluster representative.
        # NOTE - true "bypass the 30-minute window" immediate sending
        # is NOT implemented in this pass. That would need agents (or
        # a separate lightweight trigger) to call the batching agent
        # out-of-cycle the moment something BREAKING is found. This is
        # a known, honest v1 simplification - BREAKING signals are
        # still correctly classified, but currently wait for the next
        # scheduled run like everything else.
        urgencies = {}
        for cluster in clusters:
            representative = max(cluster, key=lambda s: s.composite_score)
            urgencies[str(representative.id)] = await classify_urgency(representative)

        batch, top_signals = await assemble_batch(session, clusters)
        messages = await compose_messages(top_signals)

        await push_batch(str(batch.id), messages)

        logger.info(
            "Batching agent run complete",
            extra={"extra_fields": {
                "unsent_signals": len(unsent_signals),
                "clusters": len(clusters),
                "batch_id": str(batch.id),
                "messages_composed": len(messages),
                "urgencies": urgencies,
            }},
        )

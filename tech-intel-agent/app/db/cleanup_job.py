from datetime import datetime, timedelta, timezone

from app.core.logging_config import get_logger
from app.db.repository_news import (
    soft_delete_old_signals,
    hard_delete_old_signals,
    soft_delete_old_rejected_signals,
    hard_delete_old_rejected_signals,
)
from app.db.session_news import get_news_session

logger = get_logger(__name__)

SOFT_DELETE_AFTER_DAYS = 30
HARD_DELETE_AFTER_DAYS = 90


async def run_cleanup_job() -> None:
    """
    Triggered weekly (Sunday 2am) by agents/scheduler.py.

    Two-stage cleanup: signals older than 30 days are soft-deleted -
    excluded from RAG search and future notifications, but still
    recoverable. Signals older than 90 days that were ALREADY
    soft-deleted get permanently removed.

    The rejection cache is expired on the SAME two cutoffs. Letting it
    grow forever would mean a url rejected once is never reconsidered,
    so a judgment made under an older prompt or a different composite
    threshold would silently exclude that item permanently. Expiring
    entries lets anything still in a source listing be re-judged under
    current rules, at the cost of one LLM call every 30 days rather
    than one every run.
    """
    now = datetime.now(timezone.utc)
    soft_cutoff = now - timedelta(days=SOFT_DELETE_AFTER_DAYS)
    hard_cutoff = now - timedelta(days=HARD_DELETE_AFTER_DAYS)

    async with get_news_session() as session:
        soft_count = await soft_delete_old_signals(session, soft_cutoff)
        hard_count = await hard_delete_old_signals(session, hard_cutoff)
        rejected_soft_count = await soft_delete_old_rejected_signals(session, soft_cutoff)
        rejected_hard_count = await hard_delete_old_rejected_signals(session, hard_cutoff)

    logger.info(
        "Cleanup job complete",
        extra={"extra_fields": {
            "soft_deleted": soft_count,
            "hard_deleted": hard_count,
            "rejected_soft_deleted": rejected_soft_count,
            "rejected_hard_deleted": rejected_hard_count,
        }},
    )

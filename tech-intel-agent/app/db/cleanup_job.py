from datetime import datetime, timedelta, timezone

from app.core.logging_config import get_logger
from app.db.repository_news import soft_delete_old_signals, hard_delete_old_signals
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
    """
    now = datetime.now(timezone.utc)
    soft_cutoff = now - timedelta(days=SOFT_DELETE_AFTER_DAYS)
    hard_cutoff = now - timedelta(days=HARD_DELETE_AFTER_DAYS)

    async with get_news_session() as session:
        soft_count = await soft_delete_old_signals(session, soft_cutoff)
        hard_count = await hard_delete_old_signals(session, hard_cutoff)

    logger.info(
        "Cleanup job complete",
        extra={"extra_fields": {"soft_deleted": soft_count, "hard_deleted": hard_count}},
    )

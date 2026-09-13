from fastapi.responses import JSONResponse
from fastapi import APIRouter
from sqlalchemy import text

from app.core.logging_config import get_logger

from app.db.session_news import news_engine
from app.db.session_conversation import conversation_engine
from app.queues.redis_client import ping_redis

logger = get_logger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Pinged by AWS/Railway to know this process is genuinely healthy,
    not just running. Checks both database engines and Redis are
    actually reachable - a process that never crashes but can't reach
    its dependencies should still be caught by this and trigger a
    restart.
    """
    try:
        async with news_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        async with conversation_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await ping_redis()
        return {"status": "healthy"}
    except Exception as e:
        # The detail goes to the LOG, not the response body. This
        # endpoint is unauthenticated and reachable by whoever can hit
        # the load balancer, and the failure it exists to report is
        # exactly the one whose message carries internal detail - a
        # connection error names the database user and host.
        #
        # Built with JSONResponse rather than an f-string into a Response
        # body: the previous version interpolated the raw exception text
        # straight into JSON, so any quote or backslash in the message
        # produced output the caller could not parse - on the one code
        # path this endpoint exists for.
        logger.error(
            "Health check failed",
            extra={"extra_fields": {"error": str(e), "error_type": type(e).__name__}},
        )
        return JSONResponse(
            content={"status": "unhealthy"},
            status_code=503,
        )

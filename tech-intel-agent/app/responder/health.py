from fastapi import APIRouter, Response
from sqlalchemy import text

from app.db.session_news import news_engine
from app.db.session_conversation import conversation_engine
from app.queues.redis_client import ping_redis

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
        return Response(
            content=f'{{"status": "unhealthy", "error": "{str(e)[:200]}"}}',
            status_code=503,
            media_type="application/json",
        )

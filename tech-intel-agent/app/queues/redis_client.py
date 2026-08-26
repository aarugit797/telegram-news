import json
import redis.asyncio as redis

from app.core.config import settings

# Redis supports multiple logical databases (0-15) on one instance -
# this is namespacing WITHIN one Redis process, not physical
# separation. DB 0 = signal queue, DB 1 = dead letter queue, DB 2 =
# cache/rate-limiting (used later by the responder).
_signal_queue_client = redis.from_url(settings.redis_url, db=0, decode_responses=True)
_dead_letter_client = redis.from_url(settings.redis_url, db=1, decode_responses=True)

SIGNAL_QUEUE_KEY = "signal_queue"
DEAD_LETTER_KEY = "dead_letter_queue"


async def push_signal(signal_id: str) -> None:
    """
    Called by every agent right after successfully writing an
    approved signal to the News DB. LPUSH adds to one end of a Redis
    list; the batching agent drains with RPOP from the other end -
    together this gives FIFO order (oldest signal processed first).
    """
    await _signal_queue_client.lpush(SIGNAL_QUEUE_KEY, signal_id)


async def pop_signal() -> str | None:
    """
    Used by the batching agent. Returns None if the queue is
    currently empty rather than blocking/erroring.
    """
    return await _signal_queue_client.rpop(SIGNAL_QUEUE_KEY)


async def push_dead_letter(context: dict) -> None:
    """
    Used when a single item's processing fails even after being
    caught by its own try/except. Stored as a JSON string so the
    full failure context survives for manual inspection instead of
    being silently lost.
    """
    await _dead_letter_client.lpush(DEAD_LETTER_KEY, json.dumps(context))
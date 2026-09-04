import json
import redis.asyncio as redis

from app.core.config import settings

# Redis supports multiple logical databases (0-15) on one instance -
# this is namespacing WITHIN one Redis process, not physical
# separation. DB 0 = signal queue, DB 1 = dead letter queue, DB 2 =
# cache/rate-limiting (used later by the responder).
_signal_queue_client = redis.from_url(settings.redis_url, db=0, decode_responses=True)
_dead_letter_client = redis.from_url(settings.redis_url, db=1, decode_responses=True)
_cache_client = redis.from_url(settings.redis_url, db=2, decode_responses=True)

SIGNAL_QUEUE_KEY = "signal_queue"
DEAD_LETTER_KEY = "dead_letter_queue"
DELIVERY_QUEUE_KEY = "delivery_queue"
RATE_LIMIT_PREFIX = "rate_limit:"
RATE_LIMIT_TTL_SECONDS = 86400
CACHE_PREFIX = "cache:"
CACHE_TTL_SECONDS = 600


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


async def push_batch_for_delivery(batch_id: str, messages: list[str]) -> None:
    """
    Used by processor/batching_agent.py right after composing a
    batch's messages. Stores the batch_id alongside the messages so
    the sender can report delivery status back against this specific
    batch afterward.
    """
    payload = json.dumps({"batch_id": batch_id, "messages": messages})
    await _signal_queue_client.lpush(DELIVERY_QUEUE_KEY, payload)


async def pop_batch_for_delivery() -> dict | None:
    """Used by sender/twilio_sender.py to drain the next batch ready to send."""
    raw = await _signal_queue_client.rpop(DELIVERY_QUEUE_KEY)
    return json.loads(raw) if raw else None


async def increment_rate_limit(user_id: str) -> int:
    """
    Used by responder/rate_limit.py. Increments today's message
    count for this user, returns the new count. TTL is only set on
    the FIRST increment of the day - INCR on an existing key does
    not reset its TTL on its own, so we check count == 1 to know
    this key was just created.
    """
    key = f"{RATE_LIMIT_PREFIX}{user_id}"
    count = await _cache_client.incr(key)
    if count == 1:
        await _cache_client.expire(key, RATE_LIMIT_TTL_SECONDS)
    return count


async def cache_get(key: str) -> str | None:
    """Used by responder tools to skip a repeat LLM call for an identical recent question."""
    return await _cache_client.get(f"{CACHE_PREFIX}{key}")


async def cache_set(key: str, value: str) -> None:
    await _cache_client.set(f"{CACHE_PREFIX}{key}", value, ex=CACHE_TTL_SECONDS)


async def ping_redis() -> bool:
    """Used by responder/health.py - confirms Redis is actually reachable, not just that the process is running."""
    return await _cache_client.ping()

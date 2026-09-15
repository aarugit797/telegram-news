import json
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import redis.asyncio as redis

from app.core.config import settings


def _client_for_db(db: int) -> redis.Redis:
    """
    Builds a client pinned to a specific logical database.

    Why this exists instead of redis.from_url(url, db=N): redis-py
    parses the URL and then lets the parsed options OVERRIDE the
    keyword arguments, not the other way round. Our REDIS_URL ends in
    "/0", so that "0" silently won every time and all three clients
    below landed on db 0 - no error, no warning. The namespacing this
    module documents simply was not happening, and a FLUSHDB on what
    looked like the cache would have taken the signal queue and dead
    letter queue with it.

    The fix is to remove the only part of the URL that conflicts - the
    path - and pass db explicitly. Stripping the path is deliberately
    preferred over parsing out host/port and rebuilding the client
    from scratch: everything else the URL can carry (password, the
    rediss:// TLS scheme, query parameters) survives untouched, where
    a host/port reconstruction would quietly drop them.

    The path is only stripped for redis:// and rediss:// URLs. For a
    unix:// socket URL the path IS the socket location, so removing it
    would break the connection rather than fix it.
    """
    url = settings.redis_url
    parts = urlsplit(url)
    if parts.scheme in ("redis", "rediss"):
        url = urlunsplit(parts._replace(path=""))
    return redis.from_url(url, db=db, decode_responses=True)


# Redis supports multiple logical databases (0-15) on one instance -
# this is namespacing WITHIN one Redis process, not physical
# separation. DB 0 = delivery queue, DB 1 = dead letter queue, DB 2 =
# cache/rate-limiting.
#
# DB 0 HELD A SIGNAL QUEUE THAT NOTHING READ. All five agents pushed
# every approved signal id onto it and no consumer ever existed - the
# batching agent reads unsent signals from Postgres instead, because
# Redis is not durable across a restart and a signal that has already
# been fetched, filtered, scored and embedded must not be lost. That
# was the right call; the push was simply never removed, so the key
# grew without bound and the module advertised a work queue the system
# does not use.
_delivery_client = _client_for_db(0)
_dead_letter_client = _client_for_db(1)
_cache_client = _client_for_db(2)

DEAD_LETTER_KEY = "dead_letter_queue"
DELIVERY_QUEUE_KEY = "delivery_queue"
DELIVERY_INFLIGHT_KEY = "delivery_inflight"
RATE_LIMIT_PREFIX = "rate_limit:"
RATE_LIMIT_TTL_SECONDS = 86400
CACHE_PREFIX = "cache:"
CACHE_TTL_SECONDS = 600

# LLM budget keys. Both live on the cache client (db 2) because they are
# coordination state, not queue data.
# Budgets are now PER CREDENTIAL. Each credential gets its own sorted set
# and its own daily counter, because the premise test confirmed Gemini
# quota is enforced per project: one key returning 429 while its siblings
# served normally. A single shared budget would have throttled the pool
# to one project's worth of capacity.
#
# Keys are namespaced by a short credential id ("gemini-1", "groq-1") and
# NEVER by the API key itself - these strings land in logs and Redis.
LLM_RPM_PREFIX = "llm:rpm:"
LLM_RPD_PREFIX = "llm:rpd:"
LLM_COOLDOWN_PREFIX = "llm:cooldown:"
_RPM_WINDOW_MS = 60_000


# Sliding window log. A plain INCR with a 60s TTL would be a FIXED
# window, which permits a double-rate burst straddling the boundary -
# 10 calls at 0:59 and 10 more at 1:01 is 20 calls in 2 seconds while
# never showing a count above 10. That is the same flaw already fixed in
# the per-user rate limiter, and at a 10 RPM ceiling it is the
# difference between working and being throttled.
#
# Lua because this must be ATOMIC. The whole point is coordinating two
# separate OS processes; a read-then-write from Python would let both
# see 9 used slots and both proceed.
_RPM_ACQUIRE_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local used = redis.call('ZCARD', key)
if used < limit then
  redis.call('ZADD', key, now, member)
  -- Expiry so a dead process cannot leave this key behind forever.
  redis.call('PEXPIRE', key, window + 5000)
  return {1, used + 1}
end
return {0, used}
"""

# Same atomicity argument for the daily counter.
_RPD_ACQUIRE_LUA = """
local key   = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl   = tonumber(ARGV[2])

local used = tonumber(redis.call('GET', key) or '0')
if used < limit then
  local now = redis.call('INCR', key)
  if now == 1 then
    redis.call('EXPIRE', key, ttl)
  end
  return {1, now}
end
return {0, used}
"""

_rpm_script = _cache_client.register_script(_RPM_ACQUIRE_LUA)
_rpd_script = _cache_client.register_script(_RPD_ACQUIRE_LUA)


async def try_acquire_llm_slot(credential_id: str, limit: int) -> tuple[bool, int]:
    """
    Attempts to take one slot in THIS CREDENTIAL's 60-second window.

    `limit` is passed per-call rather than read from config, because the
    two lanes check the SAME window against DIFFERENT ceilings: the
    pipeline against (rpm - reserved), the responder against the full
    rpm. One window with two limits is what reserves capacity for a
    waiting user without giving the responder a separate budget it could
    leave unused.

    Returns (acquired, slots_used_after).
    """
    acquired, used = await _rpm_script(
        keys=[f"{LLM_RPM_PREFIX}{credential_id}"],
        args=[int(time.time() * 1000), _RPM_WINDOW_MS, limit, uuid.uuid4().hex],
    )
    return bool(acquired), int(used)


async def llm_slots_used(credential_id: str) -> int:
    """
    Slots consumed in this credential's current window. Read-only - used
    by selection to rank credentials by remaining headroom, which is why
    strict round-robin was rejected: round-robin keeps handing work to a
    credential that is already at its ceiling.
    """
    key = f"{LLM_RPM_PREFIX}{credential_id}"
    now_ms = int(time.time() * 1000)
    await _cache_client.zremrangebyscore(key, 0, now_ms - _RPM_WINDOW_MS)
    return int(await _cache_client.zcard(key))


async def mark_llm_cooldown(credential_id: str, seconds: float) -> None:
    """
    Parks a credential after a 429. This is the state that stops the
    fallback chain rotating straight back into a key that just told us it
    was exhausted - without it, selection would re-pick the same
    credential the moment it had nominal headroom.
    """
    await _cache_client.set(
        f"{LLM_COOLDOWN_PREFIX}{credential_id}", "1", ex=max(int(seconds), 1)
    )


async def llm_cooldown_remaining(credential_id: str) -> int:
    """Seconds left on this credential's cooldown, 0 if it is available."""
    ttl = await _cache_client.ttl(f"{LLM_COOLDOWN_PREFIX}{credential_id}")
    return ttl if ttl and ttl > 0 else 0


async def clear_llm_cooldown(credential_id: str) -> None:
    """For tests and manual intervention."""
    await _cache_client.delete(f"{LLM_COOLDOWN_PREFIX}{credential_id}")


def _seconds_until_midnight(tz_name: str) -> int:
    """
    TTL for the daily counter, anchored to the same timezone the
    schedulers use - a day that rolls over at UTC midnight while arXiv
    fires at 08:20 Asia/Kolkata would reset the budget mid-morning.
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((midnight - now).total_seconds()), 60)


async def try_acquire_llm_daily(credential_id: str, limit: int, tz_name: str) -> tuple[bool, int]:
    """
    Attempts to take one request from THIS CREDENTIAL's daily budget.
    Like the RPM window, one counter per credential, checked against
    different ceilings by the two lanes.

    Returns (acquired, requests_used_after).
    """
    tz = ZoneInfo(tz_name)
    key = f"{LLM_RPD_PREFIX}{credential_id}:{datetime.now(tz).date().isoformat()}"
    acquired, used = await _rpd_script(
        keys=[key], args=[limit, _seconds_until_midnight(tz_name)]
    )
    return bool(acquired), int(used)


async def llm_daily_used(credential_id: str, tz_name: str) -> int:
    """Requests this credential has spent today. Read-only."""
    tz = ZoneInfo(tz_name)
    key = f"{LLM_RPD_PREFIX}{credential_id}:{datetime.now(tz).date().isoformat()}"
    return int(await _cache_client.get(key) or 0)


async def llm_budget_snapshot(credential_ids: list[str], tz_name: str) -> dict:
    """Read-only view of every credential's budgets, for logging and tests."""
    out = {}
    for cid in credential_ids:
        out[cid] = {
            "rpm_used": await llm_slots_used(cid),
            "rpd_used": await llm_daily_used(cid, tz_name),
            "cooldown_s": await llm_cooldown_remaining(cid),
        }
    return out


async def reset_llm_budgets(credential_ids: list[str], tz_name: str) -> None:
    """Clears every credential's budgets and cooldowns. Tests only."""
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date().isoformat()
    for cid in credential_ids:
        await _cache_client.delete(f"{LLM_RPM_PREFIX}{cid}")
        await _cache_client.delete(f"{LLM_RPD_PREFIX}{cid}:{today}")
        await _cache_client.delete(f"{LLM_COOLDOWN_PREFIX}{cid}")


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
    await _delivery_client.lpush(DELIVERY_QUEUE_KEY, payload)


async def pop_batch_for_delivery() -> dict | None:
    """
    DESTRUCTIVE read - kept only for callers that genuinely want
    fire-and-forget. Prefer claim_batch_for_delivery below.

    Removing the batch before it is delivered makes this stage
    at-most-once: if the send then fails, the payload is gone from Redis
    while its signals are already flagged is_sent, so nothing re-batches
    them and the messages are silently never delivered.
    """
    raw = await _delivery_client.rpop(DELIVERY_QUEUE_KEY)
    return json.loads(raw) if raw else None


async def claim_batch_for_delivery() -> tuple[dict, str] | None:
    """
    Reliable-queue claim. Atomically moves the oldest batch from the
    delivery queue onto an in-flight list, so it is owned by this worker
    but NOT yet destroyed.

    The caller must then call exactly one of:
      complete_batch_delivery(raw) - delivery succeeded, drop it
      release_batch_delivery(raw)  - delivery failed, put it back

    A batch left on the in-flight list is a crash that happened
    mid-send: recoverable by inspection, rather than lost silently.

    Returns (payload, raw) - the raw JSON string is returned alongside
    the parsed payload because LREM matches on the exact stored string.
    Re-serialising the dict would usually produce the same bytes, but
    "usually" is not a property to build queue correctness on.
    """
    raw = await _delivery_client.rpoplpush(DELIVERY_QUEUE_KEY, DELIVERY_INFLIGHT_KEY)
    if not raw:
        return None
    return json.loads(raw), raw


async def complete_batch_delivery(raw: str) -> None:
    """Delivery succeeded - remove the batch from the in-flight list."""
    await _delivery_client.lrem(DELIVERY_INFLIGHT_KEY, 1, raw)


async def release_batch_delivery(raw: str) -> None:
    """
    Delivery failed - return the batch to the queue so a later run
    retries it. RPUSH rather than LPUSH so it lands at the end the
    consumer reads from, preserving FIFO order.
    """
    await _delivery_client.lrem(DELIVERY_INFLIGHT_KEY, 1, raw)
    await _delivery_client.rpush(DELIVERY_QUEUE_KEY, raw)


async def inflight_batches() -> list[str]:
    """Batches claimed but neither completed nor released - i.e. crashed mid-send."""
    return await _delivery_client.lrange(DELIVERY_INFLIGHT_KEY, 0, -1)


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

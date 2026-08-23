"""
STUB - to be built.

WHAT: All Redis interaction for the entire system lives here - one
shared client wrapping the 3 logical databases we designed:
  DB 0 = Signal Queue (agents push, batching agent pops)
  DB 1 = Dead Letter Queue (failed signals after exhausted retries)
  DB 2 = Response Cache + Rate Limiting (per-user daily counts, cost)

WHY: Every component that touches Redis should go through one place
rather than each file managing its own Redis connection and key
naming conventions - avoids subtle bugs like two files using
different key formats for the same logical data.

INPUT: Varies by function - push_signal(id), pop_signal(),
push_dead_letter(id, error), get_rate_limit(user_id),
increment_rate_limit(user_id), cache_get(key), cache_set(key, value).

OUTPUT: Varies by function.

CONNECTS TO: Used by all 5 agents (push_signal, push_dead_letter),
processor/batching_agent.py (pop_signal), responder/rate_limit.py
(rate limit functions), responder/tools/* (cache functions).
"""

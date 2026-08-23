"""
STUB - to be built.

WHAT: Checks the user's message count for today against a 50/day
cap, stored in Redis DB 2 with a 24-hour TTL.

WHY: Prevents any single user (malicious or accidental) from running
up LLM costs or hammering the system.

INPUT: WhatsApp number (str).

OUTPUT: Boolean (under limit or not).

CONNECTS TO: Second check called from webhook.py, after whitelist.py
passes. Uses queues/redis_client.py.
"""

"""
STUB - to be built.

WHAT: Calls the Tavily Search API for questions about very recent
events not yet in the News DB, returns top 3 results, and
synthesizes an answer citing the source explicitly. Rate limited to
10 calls/user/day via Redis.

WHY: Covers the gap where a user asks about something that happened
in the last hour that our scheduled agents haven't caught yet.

INPUT: User's question text, user_id (for rate limit check).

OUTPUT: Synthesized answer string + cited source URLs.

CONNECTS TO: Called by conversational_agent.py when intent =
WEB_QUESTION. Uses Tavily API directly, queues/redis_client.py for
rate limiting, core/llm_client.py for synthesis.
"""

"""
STUB - to be built.

WHAT: Every actual database query against the Conversation DB lives
here - functions like get_recent_messages(user_id, limit),
save_message(), is_user_whitelisted(number), get_daily_cost(user_id),
increment_message_count().

WHY: Same reasoning as repository_news.py - centralizes all
Conversation DB access in one place.

INPUT: Varies per function.

OUTPUT: Varies per function - message lists, user objects, cost totals.

CONNECTS TO: Called by responder/whitelist.py, rate_limit.py,
cost_tracker.py, guardrail.py, conversational_agent.py, token_budget.py.
"""

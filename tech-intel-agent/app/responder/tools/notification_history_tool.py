"""
STUB - to be built.

WHAT: Looks up the most recent batch sent to this specific user
(via WhatsApp number + sent_at + batch_id in News DB), retrieves the
full stored content of those exact signals, and answers follow-up
questions strictly from that content using
prompts/responder/notification_followup.py.

WHY: Handles "tell me more about that repo you mentioned" - needs
to know exactly what was sent to THIS user, not just search generally.

INPUT: User's question text, user_id.

OUTPUT: Synthesized answer string referencing the specific
notification it came from.

CONNECTS TO: Called by conversational_agent.py when intent =
NOTIFICATION_FOLLOWUP. Uses db/repository_news.py (needs to know
which batch went to which user - a link tracked in News DB),
core/llm_client.py.
"""

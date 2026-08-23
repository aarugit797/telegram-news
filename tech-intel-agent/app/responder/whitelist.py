"""
STUB - to be built.

WHAT: Checks if the incoming user's WhatsApp number exists in the
Conversation DB's users table with is_whitelisted=True.

WHY: For controlled testing with a known set of friends - anyone
not on the list gets a fixed "invite-only" response with zero LLM
cost incurred.

INPUT: WhatsApp number (str).

OUTPUT: Boolean.

CONNECTS TO: First check called from webhook.py's background task.
Uses db/repository_conversation.py.
"""

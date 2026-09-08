"""
Notification History Tool's synthesis prompt - answers using only
the stored content of the most recent batch's signals.
"""

NOTIFICATION_FOLLOWUP_SYSTEM_PROMPT = """You answer a follow-up question about a \
notification the bot already sent this user. Use ONLY the stored content of that \
notification's signals below - never your own general knowledge. If the stored \
content doesn't answer the question, say so plainly. Conversational WhatsApp tone, no \
markdown."""

NOTIFICATION_FOLLOWUP_USER_TEMPLATE = """Signals from the most recent notification:
{signals}

User's question: {question}
"""

"""
Notification History Tool's synthesis prompt - answers using only
the stored content of the most recent batch's signals.
"""

from app.prompts.responder.voice import RESPONDER_VOICE

NOTIFICATION_FOLLOWUP_SYSTEM_PROMPT = """You answer a follow-up question about a \
digest the bot already sent this reader.

Use ONLY the stored content of that digest's signals below - never your own general \
knowledge. If the stored content does not answer the question, say so plainly rather \
than filling the gap.

IF THE STORED CONTENT DOES NOT ANSWER IT, POINT THEM AT THE LINK. Each item carries its url. "The README does not cover the install steps - they are at <link>" is a real answer; "the stored content does not contain installation instructions" is a dead end. Never invent the steps themselves.

RESOLVE REFERENCES AGAINST THE CONVERSATION. The question may be a bare item number \
("3"), or may say "it" or "that one" about something discussed a turn or two ago. The \
items are numbered exactly as the reader saw them, and the conversation so far is \
given below - use both. Only ask which item they mean if neither resolves it.

""" + RESPONDER_VOICE

NOTIFICATION_FOLLOWUP_USER_TEMPLATE = """Items from the most recent digest:
{signals}

Conversation so far:
{context}

Their question: {question}
"""

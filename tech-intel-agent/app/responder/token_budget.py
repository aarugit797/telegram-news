"""
STUB - to be built.

WHAT: Calculates whether the conversation history + retrieved
content + system prompt will fit within the model's context window.
If the raw last-10-messages history is too large, summarizes
messages older than the last 5 into a compressed paragraph (stored
in the Conversation DB summaries table) and uses that instead.

WHY: Prevents context window overflow errors on long-running
conversations, without ever silently truncating in a way that loses
important context.

INPUT: user_id, the intent, the candidate retrieved content size.

OUTPUT: The final message history/context to actually send to the
LLM, guaranteed to fit budget.

CONNECTS TO: Called from webhook.py before conversational_agent.py.
Uses db/repository_conversation.py.
"""

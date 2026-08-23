"""
STUB - to be built.

WHAT: Prompt that synthesizes an answer STRICTLY from retrieved News
DB content (via pgvector search) - explicitly instructed to say
"I don't have that in my database" rather than fill gaps from
general knowledge.

WHY: This is the core anti-hallucination guarantee of the whole
product - the agent must never present its own training knowledge
as if it were verified news content.

CONNECTS TO: Used by responder/tools/news_db_tool.py.
"""

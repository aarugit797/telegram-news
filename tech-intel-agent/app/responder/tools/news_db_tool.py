"""
STUB - to be built.

WHAT: Converts the user's query to a vector via core/embeddings.py,
runs a pgvector similarity search against the News DB (top 3
results), and synthesizes an answer STRICTLY from that retrieved
content using prompts/responder/news_qa.py.

WHY: This is the RAG tool and the core anti-hallucination guarantee -
if retrieved signals don't contain the answer, it says so explicitly
rather than filling gaps from the model's general training knowledge.

INPUT: User's question text.

OUTPUT: Synthesized answer string + which signals it was grounded in
(for response_composer.py to reference by source).

CONNECTS TO: Called by conversational_agent.py when intent =
NEWS_QUERY. Uses core/embeddings.py, db/repository_news.py,
core/llm_client.py.
"""

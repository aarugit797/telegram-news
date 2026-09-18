from app.core.embeddings import get_embedding
from app.core.llm_client import call_llm
from app.db.repository_news import search_signals_by_embedding
from app.db.session_news import get_news_session
from app.prompts.responder.fixed_messages import NO_MATCHING_SIGNALS
from app.prompts.responder.news_qa import NEWS_QA_SYSTEM_PROMPT, NEWS_QA_USER_TEMPLATE
from app.responder.query_rewriter import rewrite_for_retrieval


async def run_news_db_tool(question: str, context: str = "") -> tuple[str, list]:
    """
    The RAG tool. Embeds the user's question with input_type="query"
    (asymmetric embedding - deliberately different from "document",
    used when signals are first stored, see core/embeddings.py),
    searches the News DB by vector similarity, then synthesizes an
    answer STRICTLY from what was retrieved - never from the LLM's
    own general knowledge. Returns the answer text plus the signals
    it was grounded in, so response_composer.py can reference them
    by source.
    """
    # RETRIEVAL RUNS ON THE REWRITTEN QUERY, synthesis on the original.
    # "how do I install it?" embeds to a vector with no subject, so
    # pgvector returns whatever sits nearest a contextless install
    # question. Resolving the pronoun first is what makes the search
    # mean anything; the reader still gets an answer to the words they
    # actually typed.
    search_query = await rewrite_for_retrieval(question, context)
    query_embedding = await get_embedding(search_query, input_type="query")

    async with get_news_session() as session:
        signals = await search_signals_by_embedding(session, query_embedding, limit=3)

    if not signals:
        return NO_MATCHING_SIGNALS, []

    retrieved_text = "\n\n".join(
        f"Source: {s.source}\nTitle: {s.title}\nSummary: {s.summary}\n"
        + (
            f"Details: {s.full_content[:800]}"
            if getattr(s, "fetch_status", "ok") == "ok"
            else "Details: NOT AVAILABLE - the page could not be read. Only the "
                 "summary above is known about this item."
        )
        for s in signals
    )

    result = await call_llm(
        system_prompt=NEWS_QA_SYSTEM_PROMPT,
        user_message=NEWS_QA_USER_TEMPLATE.format(
            retrieved_signals=retrieved_text,
            context=context or "(no earlier messages)",
            question=question,
        ),
        trace_name="news-db-tool",
        temperature=0.3,
        # Reserved lane - a user is waiting on this reply.
        lane="responder",
    )

    return result.content, signals

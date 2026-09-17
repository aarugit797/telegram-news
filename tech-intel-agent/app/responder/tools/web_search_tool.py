from tavily import AsyncTavilyClient

from app.core.config import settings
from app.core.llm_client import call_llm
from app.prompts.responder.fixed_messages import NO_WEB_RESULTS, WEB_SEARCH_LIMIT_REACHED
from app.prompts.responder.news_qa import NEWS_QA_SYSTEM_PROMPT, NEWS_QA_USER_TEMPLATE
from app.queues.redis_client import increment_rate_limit
from app.responder.query_rewriter import rewrite_for_retrieval

_tavily_client = AsyncTavilyClient(api_key=settings.tavily_api_key)

DAILY_WEB_SEARCH_LIMIT = 10
# increment_rate_limit() already prefixes its key with "rate_limit:" -
# passing a composite string here produces a key like
# "rate_limit:web_search_limit:{user_id}", isolated from the plain
# per-user message-count key ("rate_limit:{user_id}") used by
# responder/rate_limit.py for the general 50/day cap.
WEB_SEARCH_RATE_KEY_PREFIX = "web_search_limit:"


async def run_web_search_tool(
    question: str, user_id: str, context: str = ""
) -> tuple[str, list]:
    """
    For questions about something very recent our own scheduled
    agents likely haven't caught yet. Rate limited separately and
    more tightly than the general message cap, since this tool has
    real per-call cost via the Tavily API on top of the LLM call.
    Reuses the News Q&A prompt (news_qa.py) - both tasks are "answer
    strictly from retrieved content", just with web results instead
    of stored signals as the source.
    """
    search_count = await increment_rate_limit(f"{WEB_SEARCH_RATE_KEY_PREFIX}{user_id}")
    if search_count > DAILY_WEB_SEARCH_LIMIT:
        return WEB_SEARCH_LIMIT_REACHED, []

    # Same split as news_db: the SEARCH gets the resolved query, the
    # answer is written against the original. A web search on "how do I
    # install it?" is even weaker than a bad embedding - Tavily has no
    # conversation to fall back on at all.
    #
    # Rewriting happens after the rate-limit check, so a user who is out
    # of searches does not spend an LLM call discovering that.
    search_query = await rewrite_for_retrieval(question, context)
    response = await _tavily_client.search(search_query, max_results=3)
    results = response.get("results", [])

    if not results:
        return NO_WEB_RESULTS, []

    retrieved_text = "\n\n".join(
        f"Source: {r.get('url', '')}\nTitle: {r.get('title', '')}\nDetails: {r.get('content', '')[:800]}"
        for r in results
    )

    result = await call_llm(
        system_prompt=NEWS_QA_SYSTEM_PROMPT,
        user_message=NEWS_QA_USER_TEMPLATE.format(
            retrieved_signals=retrieved_text,
            context=context or "(no earlier messages)",
            question=question,
        ),
        trace_name="web-search-tool",
        temperature=0.3,
        # Reserved lane - a user is waiting on this reply.
        lane="responder",
    )

    return result.content, results

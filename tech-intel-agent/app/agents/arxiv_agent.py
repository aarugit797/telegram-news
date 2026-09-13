import feedparser
import httpx
from datetime import datetime, timedelta, timezone

from app.core.embeddings import get_embedding
from app.core.sources import ARXIV_API_URL
from app.core.logging_config import get_logger
from app.db.repository_news import (
    signal_exists_by_url,
    insert_signal,
    insert_rejected_signal,
    url_was_rejected,
)
from app.db.session_news import get_news_session
from app.core.llm_client import LLMQuotaExhausted
from app.filters.hybrid_filter import run_hybrid_filter
from app.prompts.filters.arxiv_filter import (
    ARXIV_FILTER_SYSTEM_PROMPT,
    ARXIV_FILTER_USER_TEMPLATE,
    ARXIV_FILTER_JSON_SCHEMA,
)
from app.queues.redis_client import push_signal, push_dead_letter

logger = get_logger(__name__)

CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.CV"]
MAX_RESULTS = 50
ABSTRACT_WORD_THRESHOLD = 150
LOOKBACK_HOURS = 24  # matches this agent's own once-daily schedule


def _rules_check(paper: dict) -> bool:
    """Stage 1 - zero LLM cost."""
    return len(paper.get("abstract", "").split()) >= ABSTRACT_WORD_THRESHOLD


async def _fetch_new_papers() -> list[dict]:
    """
    arXiv's own structured API - one query already returns the FULL
    abstract for every result. This is why, unlike GitHub, this agent
    never calls filters/content_fetcher.py afterward - there's no
    "rest" left to fetch once a paper passes the filter.
    """
    category_query = " OR ".join(f"cat:{c}" for c in CATEGORIES)
    params = {
        "search_query": category_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": MAX_RESULTS,
    }

    async with httpx.AsyncClient(timeout=15.0) as http_client:
        try:
            response = await http_client.get(ARXIV_API_URL, params=params)
            response.raise_for_status()
        except Exception as e:
            logger.warning("Failed to fetch arXiv listing", extra={"extra_fields": {"error": str(e)}})
            return []

    feed = feedparser.parse(response.text)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    papers = []
    for entry in feed.entries:
        try:
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            continue
        if published < cutoff:
            continue  # older than our lookback window - already seen in a previous run
        papers.append({
            "title": entry.title.replace("\n", " ").strip(),
            "abstract": entry.summary.replace("\n", " ").strip(),
            "authors": ", ".join(a.get("name", "") for a in entry.get("authors", [])) or "unknown",
            "url": entry.id,
        })
    return papers


async def run_arxiv_agent() -> None:
    """Triggered once daily at 8am by agents/scheduler.py."""
    papers = await _fetch_new_papers()

    counts = {
        "fetched": len(papers), "skipped_duplicate": 0, "skipped_rejected": 0, "aborted_on_quota": 0,
        "rejected_rules": 0, "rejected_llm": 0, "approved": 0, "errors": 0,
    }

    async with get_news_session() as session:
        for paper in papers:
            paper_url = paper.get("url", "")
            try:
                if not paper_url:
                    continue

                if await signal_exists_by_url(session, paper_url):
                    counts["skipped_duplicate"] += 1
                    continue

                # Second cache, same purpose as the check above but for
                # items the LLM already judged and rejected - without it
                # anything still in the source listing is re-scored, and
                # paid for, on every run.
                if await url_was_rejected(session, paper_url):
                    counts["skipped_rejected"] += 1
                    continue

                filter_result = await run_hybrid_filter(
                    raw_data=paper,
                    rules_check=_rules_check,
                    system_prompt=ARXIV_FILTER_SYSTEM_PROMPT,
                    user_message=ARXIV_FILTER_USER_TEMPLATE.format(
                        title=paper.get("title", ""),
                        authors=paper.get("authors", ""),
                        abstract=paper.get("abstract", ""),
                    ),
                    json_schema=ARXIV_FILTER_JSON_SCHEMA,
                    trace_name="arxiv-filter",
                )

                if not filter_result.passed:
                    # Only cache LLM-stage rejections. A rules-stage
                    # rejection never reached the LLM, so re-judging it
                    # next run costs nothing and needs no row.
                    if filter_result.stage_reached == "rejected_by_llm":
                        await insert_rejected_signal(session, {
                            "url": paper_url,
                            "source": "arxiv",
                            "composite_score": filter_result.composite_score,
                            "filter_justification": filter_result.justification or "",
                        })
                    key = "rejected_rules" if filter_result.stage_reached == "rejected_by_rules" else "rejected_llm"
                    counts[key] += 1
                    continue

                # No content_fetcher call - the abstract already IS
                # the full content for this source.
                summary = filter_result.justification or paper.get("title", "")
                embedding = await get_embedding(summary, input_type="document")

                signal = await insert_signal(session, {
                    "source": "arxiv",
                    "title": paper.get("title", ""),
                    "url": paper_url,
                    "full_content": paper.get("abstract", ""),
                    "summary": summary,
                    "novelty_score": filter_result.scores["novelty"],
                    "relevance_score": filter_result.scores["relevance"],
                    "applicability_score": filter_result.scores["applicability"],
                    "composite_score": filter_result.composite_score,
                    "filter_justification": filter_result.justification,
                    "embedding": embedding,
                })

                await push_signal(str(signal.id))
                counts["approved"] += 1

            except LLMQuotaExhausted as e:
                # The shared budget is spent. Stop the whole run rather
                # than grinding through the remaining items raising the
                # same error each time.
                #
                # Deliberately NOT dead-lettered and NOT cached as
                # rejected: this item was never judged. Recording either
                # would turn 'we ran out of quota' into a permanent
                # verdict. Leaving it untouched means the next run picks
                # it up normally.
                counts["aborted_on_quota"] = 1
                logger.warning(
                    "arXiv agent aborted early - LLM quota exhausted",
                    extra={"extra_fields": {
                        "url": paper_url,
                        "error": str(e),
                        "processed_before_abort": counts,
                    }},
                )
                break

            except Exception as e:
                counts["errors"] += 1
                logger.error(
                    "Unexpected failure processing an arXiv paper",
                    extra={"extra_fields": {"paper_url": paper_url, "error": str(e)}},
                )
                await push_dead_letter({"agent": "arxiv", "paper_url": paper_url, "error": str(e)})

    logger.info("arXiv agent run complete", extra={"extra_fields": counts})

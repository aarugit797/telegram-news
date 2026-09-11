import feedparser
import httpx
from datetime import datetime, timedelta, timezone

from app.core.sources import NEWSLETTER_FEEDS
from app.core.embeddings import get_embedding
from app.core.logging_config import get_logger
from app.db.repository_news import signal_exists_by_url, insert_signal
from app.db.session_news import get_news_session
from app.filters.content_fetcher import fetch_full_content
from app.filters.hybrid_filter import run_hybrid_filter
from app.prompts.filters.rss_filter import (
    RSS_FILTER_SYSTEM_PROMPT,
    RSS_FILTER_USER_TEMPLATE,
    RSS_FILTER_JSON_SCHEMA,
)
from app.queues.redis_client import push_signal, push_dead_letter

logger = get_logger(__name__)

LOOKBACK_HOURS = 25  # matches this agent's own once-daily schedule, with buffer


def _rules_check(_: dict) -> bool:
    """
    Always True - these newsletters are already human-curated, so
    there's no numeric threshold to check. The LLM's job (rss_filter.py)
    is purely judging whether an item adds something beyond what our
    other 4 agents would likely already have caught.
    """
    return True


async def _fetch_newsletter_items() -> list[dict]:
    items: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    async with httpx.AsyncClient(timeout=15.0) as http_client:
        for newsletter_name, feed_url in NEWSLETTER_FEEDS.items():
            try:
                response = await http_client.get(feed_url)
                response.raise_for_status()
            except Exception as e:
                logger.warning(
                    "Failed to fetch newsletter feed",
                    extra={"extra_fields": {"newsletter": newsletter_name, "error": str(e)}},
                )
                continue

            feed = feedparser.parse(response.text)
            for entry in feed.entries:
                try:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    if published < cutoff:
                        continue
                except Exception:
                    pass

                items.append({
                    "newsletter_name": newsletter_name,
                    "title": entry.get("title", ""),
                    "excerpt": entry.get("summary", "")[:1000],
                    "url": entry.get("link", ""),
                })
    return items


async def run_rss_agent() -> None:
    """Triggered once daily at 9am by agents/scheduler.py."""
    items = await _fetch_newsletter_items()

    counts = {"fetched": len(items), "skipped_duplicate": 0, "rejected_llm": 0, "approved": 0, "errors": 0}

    async with get_news_session() as session:
        for item in items:
            item_url = item.get("url", "")
            try:
                if not item_url:
                    continue

                if await signal_exists_by_url(session, item_url):
                    counts["skipped_duplicate"] += 1
                    continue

                filter_result = await run_hybrid_filter(
                    raw_data=item,
                    rules_check=_rules_check,
                    system_prompt=RSS_FILTER_SYSTEM_PROMPT,
                    user_message=RSS_FILTER_USER_TEMPLATE.format(
                        newsletter_name=item.get("newsletter_name", ""),
                        title=item.get("title", ""),
                        excerpt=item.get("excerpt", ""),
                    ),
                    json_schema=RSS_FILTER_JSON_SCHEMA,
                    trace_name="rss-filter",
                )

                if not filter_result.passed:
                    counts["rejected_llm"] += 1
                    continue

                full_content = await fetch_full_content(item_url, source="rss")
                summary = filter_result.justification or item.get("title", "")
                embedding = await get_embedding(summary, input_type="document")

                signal = await insert_signal(session, {
                    "source": "rss",
                    "title": item.get("title", ""),
                    "url": item_url,
                    "full_content": full_content,
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

            except Exception as e:
                counts["errors"] = counts.get("errors", 0) + 1
                logger.error(
                    "Unexpected failure processing a newsletter item",
                    extra={"extra_fields": {"item_url": item_url, "error": str(e)}},
                )
                await push_dead_letter({"agent": "rss", "item_url": item_url, "error": str(e)})

    logger.info("RSS agent run complete", extra={"extra_fields": counts})

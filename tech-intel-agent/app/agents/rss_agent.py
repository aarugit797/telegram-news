import feedparser
import httpx
from datetime import datetime, timedelta, timezone

from app.core.sources import NEWSLETTER_FEEDS
from app.core.embeddings import get_embedding
from app.core.logging_config import get_logger
from app.db.repository_news import (
    signal_exists_by_url,
    insert_signal,
    insert_rejected_signal,
    url_was_rejected,
)
from app.db.session_news import get_news_session
from app.filters.content_fetcher import fetch_full_content
from app.core.llm_client import LLMQuotaExhausted
from app.filters.hybrid_filter import run_hybrid_filter
from app.prompts.filters.rss_filter import (
    RSS_FILTER_SYSTEM_PROMPT,
    RSS_FILTER_USER_TEMPLATE,
    RSS_FILTER_JSON_SCHEMA,
)
from app.queues.redis_client import push_signal, push_dead_letter

logger = get_logger(__name__)


# Column widths in models_news.Signal / RejectedSignal. Values are fitted
# to them BEFORE insert rather than after a failure, because an over-long
# value raises StringDataRightTruncation, which the broad except below
# swallows and dead-letters - making a systematic problem (a source that
# always emits long titles) look like a string of unrelated transient
# errors.
TITLE_MAX = 500
URL_MAX = 1000


def _fit(value: str | None, limit: int) -> str:
    """
    Trims to the column width. Note the asymmetry: a trimmed TITLE is
    still a usable title, but a trimmed URL is a broken link that will
    404 on content fetch and will not match the real URL on a later
    dedup check. Trimming still beats letting the insert raise - the row
    is visible and recoverable either way - but a URL long enough to hit
    this is pathological and worth fixing at the source rather than
    treating as normal.
    """
    text = (value or "").strip()
    return text[:limit]

# Derived from the schedule rather than hardcoded, for the reason
# blogs_agent documents: a standalone constant drifts when the scheduler
# changes and the gap is invisible - items simply stop being seen.
# The resulting 25h is unchanged; only its provenance is.
RUN_INTERVAL_HOURS = 24     # must match agents/scheduler.py's rss_agent job
LOOKBACK_BUFFER_HOURS = 1   # covers a late, delayed or slow-starting run
LOOKBACK_HOURS = RUN_INTERVAL_HOURS + LOOKBACK_BUFFER_HOURS


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

    # See blogs_agent: a 3xx does not trip raise_for_status(), so an
    # unfollowed redirect silently yields zero entries and a clean log.
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http_client:
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

    counts = {"fetched": len(items), "skipped_duplicate": 0, "skipped_rejected": 0, "aborted_on_quota": 0, "rejected_llm": 0, "approved": 0, "errors": 0}

    async with get_news_session() as session:
        for item in items:
            item_url = item.get("url", "")
            try:
                if not item_url:
                    continue

                if await signal_exists_by_url(session, item_url):
                    counts["skipped_duplicate"] += 1
                    continue

                # Second cache, same purpose as the check above but for
                # items the LLM already judged and rejected - without it
                # anything still in the source listing is re-scored, and
                # paid for, on every run.
                if await url_was_rejected(session, item_url):
                    counts["skipped_rejected"] += 1
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
                    # Only cache LLM-stage rejections. A rules-stage
                    # rejection never reached the LLM, so re-judging it
                    # next run costs nothing and needs no row.
                    if filter_result.stage_reached == "rejected_by_llm":
                        await insert_rejected_signal(session, {
                            "url": _fit(item_url, URL_MAX),
                            "source": "rss",
                            "composite_score": filter_result.composite_score,
                            "filter_justification": filter_result.justification or "",
                        })
                    counts["rejected_llm"] += 1
                    continue

                full_content = await fetch_full_content(item_url, source="rss")
                # The model's DESCRIPTION of the thing, not its verdict on it.
                #
                # This used to store filter_result.justification, which is
                # the SCORING RATIONALE ("Genuinely novel X, highly
                # relevant to Y"). The message composer was then faithfully
                # rewriting evaluations into casual language, which is why
                # notifications read like a review board. No prompt change
                # fixes bad input.
                #
                # The filter call already happens, so asking for one more
                # field costs nothing. filter_justification is still stored
                # separately below - it remains useful for auditing why a
                # threshold decision went the way it did.
                summary = (filter_result.scores or {}).get("summary") or item.get("title", "")
                embedding = await get_embedding(summary, input_type="document")

                signal = await insert_signal(session, {
                    "source": "rss",
                    "title": _fit(item.get("title", ""), TITLE_MAX),
                    "url": _fit(item_url, URL_MAX),
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
                    "RSS agent aborted early - LLM quota exhausted",
                    extra={"extra_fields": {
                        "url": item_url,
                        "error": str(e),
                        "processed_before_abort": counts,
                    }},
                )
                break

            except Exception as e:
                # FIRST, before anything else touches the session.
                # A failed commit leaves SQLAlchemy's session holding an
                # aborted transaction it does not know about, and every
                # later query on it raises PendingRollbackError until
                # rollback() is called. Without this, one bad row - a
                # duplicate url losing a race to the partial unique
                # index, or an over-long field - turns into every
                # REMAINING item in the run failing and being
                # dead-lettered. The per-item try/except looks like
                # isolation but provides none without it.
                #
                # Each item commits independently, so this discards only
                # the failed item's work.
                await session.rollback()
                counts["errors"] = counts.get("errors", 0) + 1
                logger.error(
                    "Unexpected failure processing a newsletter item",
                    extra={"extra_fields": {"item_url": item_url, "error": str(e)}},
                )
                await push_dead_letter({"agent": "rss", "item_url": item_url, "error": str(e)})

    logger.info("RSS agent run complete", extra={"extra_fields": counts})

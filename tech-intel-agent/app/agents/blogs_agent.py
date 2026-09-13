import feedparser
import httpx
from datetime import datetime, timedelta, timezone

from app.core.embeddings import get_embedding
from app.core.logging_config import get_logger
from app.core.sources import BLOG_FEEDS
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
from app.prompts.filters.blogs_filter import (
    BLOGS_FILTER_SYSTEM_PROMPT,
    BLOGS_FILTER_USER_TEMPLATE,
    BLOGS_FILTER_JSON_SCHEMA,
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

LOOKBACK_HOURS = 1  # matches this agent's own 30-minute schedule, with buffer


def _rules_check(_: dict) -> bool:
    """
    Always True - official lab blogs are inherently credible, so
    there's no numeric threshold to check. The LLM novelty judgment
    (blogs_filter.py) is the only real filter for this source.
    """
    return True


async def _fetch_blog_posts() -> list[dict]:
    posts: list[dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

    async with httpx.AsyncClient(timeout=15.0) as http_client:
        for lab_name, feed_url in BLOG_FEEDS.items():
            try:
                response = await http_client.get(feed_url)
                response.raise_for_status()
            except Exception as e:
                logger.warning(
                    "Failed to fetch blog feed",
                    extra={"extra_fields": {"lab": lab_name, "error": str(e)}},
                )
                continue

            feed = feedparser.parse(response.text)
            for entry in feed.entries:
                try:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    if published < cutoff:
                        continue
                except Exception:
                    pass  # some feeds omit a reliable timestamp - don't drop the entry over that alone

                posts.append({
                    "lab_name": lab_name,
                    "title": entry.get("title", ""),
                    "excerpt": entry.get("summary", "")[:1000],
                    "url": entry.get("link", ""),
                })
    return posts


async def run_blogs_agent() -> None:
    """Triggered every 30 minutes by agents/scheduler.py."""
    posts = await _fetch_blog_posts()

    counts = {"fetched": len(posts), "skipped_duplicate": 0, "skipped_rejected": 0, "aborted_on_quota": 0, "rejected_llm": 0, "approved": 0, "errors": 0}

    async with get_news_session() as session:
        for post in posts:
            post_url = post.get("url", "")
            try:
                if not post_url:
                    continue

                if await signal_exists_by_url(session, post_url):
                    counts["skipped_duplicate"] += 1
                    continue

                # Second cache, same purpose as the check above but for
                # items the LLM already judged and rejected - without it
                # anything still in the source listing is re-scored, and
                # paid for, on every run.
                if await url_was_rejected(session, post_url):
                    counts["skipped_rejected"] += 1
                    continue

                filter_result = await run_hybrid_filter(
                    raw_data=post,
                    rules_check=_rules_check,
                    system_prompt=BLOGS_FILTER_SYSTEM_PROMPT,
                    user_message=BLOGS_FILTER_USER_TEMPLATE.format(
                        lab_name=post.get("lab_name", ""),
                        title=post.get("title", ""),
                        excerpt=post.get("excerpt", ""),
                    ),
                    json_schema=BLOGS_FILTER_JSON_SCHEMA,
                    trace_name="blogs-filter",
                )

                if not filter_result.passed:
                    # Only cache LLM-stage rejections. A rules-stage
                    # rejection never reached the LLM, so re-judging it
                    # next run costs nothing and needs no row.
                    if filter_result.stage_reached == "rejected_by_llm":
                        await insert_rejected_signal(session, {
                            "url": _fit(post_url, URL_MAX),
                            "source": "blogs",
                            "composite_score": filter_result.composite_score,
                            "filter_justification": filter_result.justification or "",
                        })
                    counts["rejected_llm"] += 1
                    continue

                full_content = await fetch_full_content(post_url, source="blogs")
                summary = filter_result.justification or post.get("title", "")
                embedding = await get_embedding(summary, input_type="document")

                signal = await insert_signal(session, {
                    "source": "blogs",
                    "title": _fit(post.get("title", ""), TITLE_MAX),
                    "url": _fit(post_url, URL_MAX),
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
                    "Blogs agent aborted early - LLM quota exhausted",
                    extra={"extra_fields": {
                        "url": post_url,
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
                # REMAINING post in the run failing and being
                # dead-lettered. The per-item try/except looks like
                # isolation but provides none without it.
                #
                # Each item commits independently, so this discards only
                # the failed item's work.
                await session.rollback()
                counts["errors"] = counts.get("errors", 0) + 1
                logger.error(
                    "Unexpected failure processing a blog post",
                    extra={"extra_fields": {"post_url": post_url, "error": str(e)}},
                )
                await push_dead_letter({"agent": "blogs", "post_url": post_url, "error": str(e)})

    logger.info("Blogs agent run complete", extra={"extra_fields": counts})

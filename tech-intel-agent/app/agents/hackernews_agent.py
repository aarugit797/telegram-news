import httpx

from app.core.embeddings import get_embedding
from app.core.sources import HN_TOP_STORIES_URL, HN_ITEM_URL
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
from app.prompts.filters.hackernews_filter import (
    HACKERNEWS_FILTER_SYSTEM_PROMPT,
    HACKERNEWS_FILTER_USER_TEMPLATE,
    HACKERNEWS_FILTER_JSON_SCHEMA,
)
from app.queues.redis_client import push_dead_letter

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

STORIES_TO_CHECK = 30
TOP_COMMENTS_TO_FETCH = 10
SCORE_THRESHOLD = 100
COMMENT_COUNT_THRESHOLD = 20


def _rules_check(story: dict) -> bool:
    """Stage 1 - zero LLM cost. Both thresholds must clear."""
    return (
        story.get("score", 0) >= SCORE_THRESHOLD
        and story.get("descendants", 0) >= COMMENT_COUNT_THRESHOLD
    )


async def _fetch_item(http_client: httpx.AsyncClient, item_id: int) -> dict:
    response = await http_client.get(HN_ITEM_URL.format(item_id=item_id))
    response.raise_for_status()
    return response.json() or {}


async def _fetch_top_comments(http_client: httpx.AsyncClient, story: dict) -> str:
    """
    HN's API gives a story's top-level comment IDs in `kids` - we
    fetch a handful of those individually (the API has no bulk-fetch
    endpoint) and join their text, since comment quality is a key
    input to this source's LLM judgment.
    """
    comment_ids = story.get("kids", [])[:TOP_COMMENTS_TO_FETCH]
    texts = []
    for cid in comment_ids:
        try:
            comment = await _fetch_item(http_client, cid)
            if comment.get("text"):
                texts.append(comment["text"])
        except Exception:
            continue  # one bad comment fetch shouldn't block the rest
    return "\n---\n".join(texts) if texts else "(no comments available)"


async def _fetch_top_stories() -> list[dict]:
    """
    /v0/topstories.json returns up to 500 story IDs, newest-ranked
    first - we only pull the top STORIES_TO_CHECK of these, fetching
    each story's full data plus its top comments.
    """
    stories: list[dict] = []
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        try:
            response = await http_client.get(HN_TOP_STORIES_URL)
            response.raise_for_status()
            story_ids = response.json()[:STORIES_TO_CHECK]
        except Exception as e:
            logger.warning("Failed to fetch HN top story IDs", extra={"extra_fields": {"error": str(e)}})
            return []

        for story_id in story_ids:
            try:
                story = await _fetch_item(http_client, story_id)
                if story.get("url"):  # skip "Ask HN" / self-posts with no external link
                    story["top_comments"] = await _fetch_top_comments(http_client, story)
                    stories.append(story)
            except Exception as e:
                logger.warning(
                    "Failed to fetch one HN story",
                    extra={"extra_fields": {"story_id": story_id, "error": str(e)}},
                )
    return stories


async def run_hackernews_agent() -> None:
    """Triggered every 15 minutes by agents/scheduler.py."""
    stories = await _fetch_top_stories()

    counts = {
        "fetched": len(stories), "skipped_duplicate": 0, "skipped_rejected": 0, "aborted_on_quota": 0,
        "rejected_rules": 0, "rejected_llm": 0, "approved": 0, "errors": 0,
    }

    async with get_news_session() as session:
        for story in stories:
            story_url = story.get("url", "")
            try:
                if not story_url:
                    continue

                if await signal_exists_by_url(session, story_url):
                    counts["skipped_duplicate"] += 1
                    continue

                # Second cache, same purpose as the check above but for
                # items the LLM already judged and rejected - without it
                # anything still in the source listing is re-scored, and
                # paid for, on every run.
                if await url_was_rejected(session, story_url):
                    counts["skipped_rejected"] += 1
                    continue

                filter_result = await run_hybrid_filter(
                    raw_data=story,
                    rules_check=_rules_check,
                    system_prompt=HACKERNEWS_FILTER_SYSTEM_PROMPT,
                    user_message=HACKERNEWS_FILTER_USER_TEMPLATE.format(
                        title=story.get("title", ""),
                        score=story.get("score", 0),
                        comment_count=story.get("descendants", 0),
                        url=story_url,
                        top_comments=story.get("top_comments", ""),
                    ),
                    json_schema=HACKERNEWS_FILTER_JSON_SCHEMA,
                    trace_name="hackernews-filter",
                )

                if not filter_result.passed:
                    # Only cache LLM-stage rejections. A rules-stage
                    # rejection never reached the LLM, so re-judging it
                    # next run costs nothing and needs no row.
                    if filter_result.stage_reached == "rejected_by_llm":
                        await insert_rejected_signal(session, {
                            "url": _fit(story_url, URL_MAX),
                            "source": "hackernews",
                            "composite_score": filter_result.composite_score,
                            "filter_justification": filter_result.justification or "",
                        })
                    key = "rejected_rules" if filter_result.stage_reached == "rejected_by_rules" else "rejected_llm"
                    counts[key] += 1
                    continue

                full_content, fetch_status = await fetch_full_content(story_url, source="hackernews")
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
                summary = (filter_result.scores or {}).get("summary") or story.get("title", "")
                embedding = await get_embedding(summary, input_type="document")

                signal = await insert_signal(session, {
                    "source": "hackernews",
                    "title": _fit(story.get("title", ""), TITLE_MAX),
                    "url": _fit(story_url, URL_MAX),
                    "full_content": full_content,
                    "fetch_status": fetch_status,
                    "summary": summary,
                    "novelty_score": filter_result.scores["novelty"],
                    "relevance_score": filter_result.scores["relevance"],
                    "applicability_score": filter_result.scores["applicability"],
                    "composite_score": filter_result.composite_score,
                    "filter_justification": filter_result.justification,
                    "embedding": embedding,
                })

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
                    "HackerNews agent aborted early - LLM quota exhausted",
                    extra={"extra_fields": {
                        "url": story_url,
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
                # REMAINING story in the run failing and being
                # dead-lettered. The per-item try/except looks like
                # isolation but provides none without it.
                #
                # Each item commits independently, so this discards only
                # the failed item's work.
                await session.rollback()
                counts["errors"] += 1
                logger.error(
                    "Unexpected failure processing an HN story",
                    extra={"extra_fields": {"story_url": story_url, "error": str(e)}},
                )
                await push_dead_letter({"agent": "hackernews", "story_url": story_url, "error": str(e)})

    logger.info("HackerNews agent run complete", extra={"extra_fields": counts})

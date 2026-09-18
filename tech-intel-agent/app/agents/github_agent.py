import re
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
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
from app.prompts.filters.github_filter import (
    GITHUB_FILTER_SYSTEM_PROMPT,
    GITHUB_FILTER_USER_TEMPLATE,
    GITHUB_FILTER_JSON_SCHEMA,
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

TARGET_LANGUAGES = [
    "python", "javascript", "typescript", "rust", "go",
    "java", "c++", "shell", "jupyter-notebook",
]
STARS_TODAY_THRESHOLD = 200


def _rules_check(repo_data: dict) -> bool:
    """
    Stage 1 - pure numeric threshold, zero LLM cost.
    currentPeriodStars is the trending API's field name for stars
    gained within the requested period (we request since=daily).
    """
    return repo_data.get("currentPeriodStars", 0) >= STARS_TODAY_THRESHOLD


def _first_int(text: str) -> int:
    """'1,234 stars today' -> 1234. Returns 0 when there is no number,
    which is the correct reading for a repo with no stars in the period."""
    match = re.search(r"[\d,]+", text or "")
    return int(match.group().replace(",", "")) if match else 0


def _parse_trending_html(html: str) -> list[dict]:
    """
    Turns one trending page into the same dict shape the rest of this
    agent already expects - notably `currentPeriodStars`, which
    _rules_check reads. Keeping the old key names means nothing
    downstream of the fetch had to change.

    Each repo is one <article class="Box-row">. A missing sub-element
    yields a falsy default rather than raising, so one malformed row
    cannot lose the other nineteen on the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    repos: list[dict] = []

    for article in soup.select("article.Box-row"):
        heading = article.select_one("h2 a")
        if heading is None or not heading.get("href"):
            continue

        owner_repo = heading["href"].strip("/")
        description = article.select_one("p")
        language = article.select_one('[itemprop="programmingLanguage"]')
        total_stars = article.select_one('a[href$="/stargazers"]')
        # The "N stars today" figure sits in its own right-floated span.
        period_stars = article.select_one("span.d-inline-block.float-sm-right")

        repos.append({
            "name": owner_repo,
            "url": f"https://github.com/{owner_repo}",
            "language": language.get_text(strip=True) if language else None,
            "stars": _first_int(total_stars.get_text() if total_stars else ""),
            "currentPeriodStars": _first_int(period_stars.get_text() if period_stars else ""),
            "description": description.get_text(strip=True) if description else "",
        })

    return repos


async def _fetch_trending_repos() -> list[dict]:
    """
    Scrapes GitHub's own trending page once per target language.

    GitHub has no official trending API. This previously called a
    third-party JSON wrapper (api.gitterapp.com), which now returns
    404 on every path - so the agent silently fetched nothing while
    still reporting a successful run. Reading GitHub's own page
    removes the dependency on a third party staying alive, at the
    cost of depending on their markup instead: if GitHub restructures
    the page, _parse_trending_html returns an empty list rather than
    raising, so watch the `fetched` count in the completion log.

    A browser User-Agent is sent because GitHub serves a reduced or
    blocked response to some default client identifiers.
    """
    all_repos: list[dict] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; tech-intel-agent/1.0)"}

    async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as http_client:
        for language in TARGET_LANGUAGES:
            try:
                # quote() because "c++" would otherwise be mangled in the path.
                response = await http_client.get(
                    f"{settings.github_trending_base}/{quote(language, safe='')}",
                    params={"since": "daily"},
                )
                response.raise_for_status()
                all_repos.extend(_parse_trending_html(response.text))
            except Exception as e:
                # One language's fetch failing shouldn't block the
                # rest - log and keep going.
                logger.warning(
                    "Trending fetch failed for one language",
                    extra={"extra_fields": {"language": language, "error": str(e)}},
                )
    return all_repos


async def run_github_agent() -> None:
    """
    Triggered every 2 hours by agents/scheduler.py.

    Flow per repo: skip if already seen (signal_exists_by_url) ->
    run_hybrid_filter (rules stage, then LLM stage only if rules
    passed) -> if passed both: fetch README (the ONE fetch for this
    repo), embed, store, push to signal queue. Every repo is wrapped
    in its own try/except so one bad repo can't take down the whole
    run.
    """
    repos = await _fetch_trending_repos()

    counts = {
        "fetched": len(repos), "skipped_duplicate": 0, "skipped_rejected": 0, "aborted_on_quota": 0,
        "rejected_rules": 0, "rejected_llm": 0, "approved": 0, "errors": 0,
    }

    async with get_news_session() as session:
        for repo in repos:
            repo_url = repo.get("url", "")
            try:
                if not repo_url:
                    continue

                if await signal_exists_by_url(session, repo_url):
                    counts["skipped_duplicate"] += 1
                    continue

                # Second cache, same purpose as the check above but for
                # items the LLM already judged and rejected - without it
                # anything still in the source listing is re-scored, and
                # paid for, on every run.
                if await url_was_rejected(session, repo_url):
                    counts["skipped_rejected"] += 1
                    continue

                filter_result = await run_hybrid_filter(
                    raw_data=repo,
                    rules_check=_rules_check,
                    system_prompt=GITHUB_FILTER_SYSTEM_PROMPT,
                    user_message=GITHUB_FILTER_USER_TEMPLATE.format(
                        repo_name=repo.get("name", ""),
                        language=repo.get("language") or "unknown",
                        stars_today=repo.get("currentPeriodStars", 0),
                        total_stars=repo.get("stars", 0),
                        description=repo.get("description") or "",
                    ),
                    json_schema=GITHUB_FILTER_JSON_SCHEMA,
                    trace_name="github-filter",
                )

                if not filter_result.passed:
                    # Only cache LLM-stage rejections. A rules-stage
                    # rejection never reached the LLM, so re-judging it
                    # next run costs nothing and needs no row.
                    if filter_result.stage_reached == "rejected_by_llm":
                        await insert_rejected_signal(session, {
                            "url": _fit(repo_url, URL_MAX),
                            "source": "github",
                            "composite_score": filter_result.composite_score,
                            "filter_justification": filter_result.justification or "",
                        })
                    key = "rejected_rules" if filter_result.stage_reached == "rejected_by_rules" else "rejected_llm"
                    counts[key] += 1
                    continue

                full_content, fetch_status = await fetch_full_content(repo_url, source="github")

                # NOTE - using the LLM's scoring justification as the
                # stored summary for now, since it already names what
                # makes the repo notable. This is a simplification
                # worth revisiting once message_composer.py exists -
                # a dedicated "describe this repo in one line" prompt
                # may produce a cleaner summary than a score
                # justification repurposed as one.
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
                summary = (filter_result.scores or {}).get("summary") or repo.get("description") or repo.get("name", "")

                embedding = await get_embedding(summary, input_type="document")

                signal = await insert_signal(session, {
                    "source": "github",
                    "title": _fit(repo.get("name", ""), TITLE_MAX),
                    "url": _fit(repo_url, URL_MAX),
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
                    "GitHub agent aborted early - LLM quota exhausted",
                    extra={"extra_fields": {
                        "url": repo_url,
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
                # REMAINING trending repo in the run failing and being
                # dead-lettered. The per-item try/except looks like
                # isolation but provides none without it.
                #
                # Each item commits independently, so this discards only
                # the failed item's work.
                await session.rollback()
                counts["errors"] += 1
                logger.error(
                    "Unexpected failure processing a trending repo",
                    extra={"extra_fields": {"repo_url": repo_url, "error": str(e)}},
                )
                await push_dead_letter({"agent": "github", "repo_url": repo_url, "error": str(e)})

    logger.info("GitHub agent run complete", extra={"extra_fields": counts})
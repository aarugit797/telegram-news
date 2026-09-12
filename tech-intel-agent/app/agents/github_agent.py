import re
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.embeddings import get_embedding
from app.core.logging_config import get_logger
from app.db.repository_news import signal_exists_by_url, insert_signal
from app.db.session_news import get_news_session
from app.filters.content_fetcher import fetch_full_content
from app.filters.hybrid_filter import run_hybrid_filter
from app.prompts.filters.github_filter import (
    GITHUB_FILTER_SYSTEM_PROMPT,
    GITHUB_FILTER_USER_TEMPLATE,
    GITHUB_FILTER_JSON_SCHEMA,
)
from app.queues.redis_client import push_signal, push_dead_letter

logger = get_logger(__name__)

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
        "fetched": len(repos), "skipped_duplicate": 0,
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
                    key = "rejected_rules" if filter_result.stage_reached == "rejected_by_rules" else "rejected_llm"
                    counts[key] += 1
                    continue

                full_content = await fetch_full_content(repo_url, source="github")

                # NOTE - using the LLM's scoring justification as the
                # stored summary for now, since it already names what
                # makes the repo notable. This is a simplification
                # worth revisiting once message_composer.py exists -
                # a dedicated "describe this repo in one line" prompt
                # may produce a cleaner summary than a score
                # justification repurposed as one.
                summary = filter_result.justification or repo.get("description") or repo.get("name", "")

                embedding = await get_embedding(summary, input_type="document")

                signal = await insert_signal(session, {
                    "source": "github",
                    "title": repo.get("name", ""),
                    "url": repo_url,
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
                counts["errors"] += 1
                logger.error(
                    "Unexpected failure processing a trending repo",
                    extra={"extra_fields": {"repo_url": repo_url, "error": str(e)}},
                )
                await push_dead_letter({"agent": "github", "repo_url": repo_url, "error": str(e)})

    logger.info("GitHub agent run complete", extra={"extra_fields": counts})
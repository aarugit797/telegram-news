import httpx

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


async def _fetch_trending_repos() -> list[dict]:
    """
    Calls the unofficial GitHub trending JSON wrapper once per
    target language. GitHub itself has no official trending API -
    this is a real reliability dependency on a third-party service,
    not something we control or can guarantee stays available.
    """
    all_repos: list[dict] = []
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        for language in TARGET_LANGUAGES:
            try:
                response = await http_client.get(
                    settings.github_trending_api_base,
                    params={"language": language, "since": "daily"},
                )
                response.raise_for_status()
                all_repos.extend(response.json())
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
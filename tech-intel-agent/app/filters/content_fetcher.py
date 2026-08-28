import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _github_headers() -> dict:
    """
    Adds an Authorization header when a token is configured -
    unauthenticated GitHub API calls are capped at 60/hour, while a
    token raises that to 5000/hour. Falls back gracefully to
    unauthenticated if no token is set, rather than failing outright.
    """
    headers = {"Accept": "application/vnd.github.raw+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


async def fetch_full_content(url: str, source: str) -> str:
    """
    Called by an agent ONLY after a signal has already passed
    hybrid_filter.py - never before, since most candidates get
    rejected and fetching full content for them would be wasted work.

    Only 3 of our 5 agents actually call this function at all:

    - GitHub: scoring uses only cheap trending-feed metadata (name,
      description, language, star counts) - no README is fetched
      before this point. This is the ONE and ONLY README fetch for a
      GitHub signal, and only for repos that already passed both
      filter stages. Uses GitHub's own structured API (own code path
      below).

    - HackerNews, Blogs/RSS: their initial fetch only had a title and
      a LINK to somewhere else - some arbitrary third-party website
      with no guaranteed structure. Generic HTML text extraction is
      the only honest option here, since we can't build a structured
      path for a destination we don't control.

    arXiv is deliberately NOT routed through this file. Its own
    structured API - used during the agent's INITIAL fetch, to
    discover new papers - already returns the full abstract directly.
    Unlike GitHub, there's no "rest" left to fetch afterward, so
    arxiv_agent.py simply carries that abstract straight through to
    the News DB without ever calling this function.
    """
    if source == "github":
        try:
            return await _fetch_github_readme(url)
        except Exception as e:
            logger.warning(
                f"Content fetch failed for source={source}",
                extra={"extra_fields": {"url": url, "error": str(e)}},
            )
            return ""

    try:
        return await _fetch_and_extract_text(url)
    except Exception as e:
        # A fetch failure here shouldn't crash the whole agent run -
        # the signal still gets stored, just with empty full_content,
        # which the agent's caller can decide how to handle.
        logger.warning(
            f"Content fetch failed for source={source}",
            extra={"extra_fields": {"url": url, "error": str(e)}},
        )
        return ""


async def _fetch_github_readme(repo_url: str) -> str:
    """
    repo_url looks like https://github.com/{owner}/{repo}. GitHub's
    dedicated README endpoint returns the raw file content directly
    and automatically finds it regardless of whether the repo's
    default branch is "main" or "master" - so we don't have to guess.
    """
    owner_repo = repo_url.rstrip("/").split("github.com/")[-1]
    api_url = f"https://api.github.com/repos/{owner_repo}/readme"

    async with httpx.AsyncClient(timeout=10.0) as http_client:
        response = await http_client.get(api_url, headers=_github_headers())
        response.raise_for_status()
        return response.text


async def _fetch_and_extract_text(url: str) -> str:
    """
    Used for HackerNews (the article a post links to) and blogs/RSS
    (the actual post). Fetches the raw HTML and strips it down to
    plain readable text - discarding scripts, styling, and markup
    that would otherwise add noise to both the embedding we generate
    from this text and any answer built from it later.
    """
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http_client:
        response = await http_client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    return soup.get_text(separator="\n", strip=True)

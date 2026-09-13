import asyncio
import ipaddress
from urllib.parse import urljoin, urlparse

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


# SSRF GUARD.
#
# The url reaching _fetch_and_extract_text comes from a HackerNews
# submission or an RSS item - attacker-controlled in practice, since
# anyone can submit a link to HN. Fetching it from inside our network,
# following redirects, and then STORING the response body makes this a
# read primitive into everything the host can reach:
#
#   http://169.254.169.254/latest/meta-data/iam/security-credentials/<role>
#
# returns temporary IAM credentials on an instance with IMDSv1 enabled.
# Those would land in signals.full_content, be embedded, and then be
# surfaced verbatim to any WhatsApp user whose question retrieves that
# row. The same reachability covers RDS and ElastiCache endpoints inside
# the VPC, which is why enforcing IMDSv2 on the instance is a necessary
# mitigation but NOT a substitute for this one.
_ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 5


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    """Anything not routable on the public internet is refused."""
    return not (
        ip.is_private          # 10/8, 172.16/12, 192.168/16, fc00::/7
        or ip.is_loopback      # 127/8, ::1
        or ip.is_link_local    # 169.254/16 - the metadata endpoint
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _assert_fetchable(url: str) -> None:
    """
    Rejects a url before any connection is made. Raises ValueError,
    which fetch_full_content already catches and logs.

    Every address the hostname resolves to is checked, not just the
    first: a name with both a public and a 127.0.0.1 record would
    otherwise pass on one lookup and connect to loopback on the next.

    KNOWN LIMIT - DNS rebinding. The name is resolved here and resolved
    again by httpx when it connects, so a record with a ~0s TTL can
    answer public now and private a moment later. Closing that fully
    means pinning the connection to the address validated here (connect
    by IP, pass the original Host header), which httpx does not expose
    cleanly. The guard below removes the trivial attack - a literal
    metadata-IP or internal-hostname url, and any redirect into one -
    and leaves a materially harder one.
    """
    parsed = urlparse(url)

    if parsed.scheme not in _ALLOWED_SCHEMES:
        # Blocks file://, gopher://, ftp:// and friends outright.
        raise ValueError(f"refusing non-http(s) scheme: {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise ValueError(f"refusing url with no host: {url[:100]!r}")

    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or 0)
    except Exception as e:
        raise ValueError(f"could not resolve {host!r}: {e}") from e

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _is_public_ip(ip):
            raise ValueError(
                f"refusing url resolving to non-public address {ip} (host={host!r})"
            )


async def _fetch_and_extract_text(url: str) -> str:
    """
    Used for HackerNews (the article a post links to) and blogs/RSS
    (the actual post). Fetches the raw HTML and strips it down to
    plain readable text - discarding scripts, styling, and markup
    that would otherwise add noise to both the embedding we generate
    from this text and any answer built from it later.

    Redirects are followed MANUALLY, one hop at a time, because
    follow_redirects=True performs the intermediate requests inside
    httpx where nothing can inspect them. A public url that 302s to
    169.254.169.254 would be fetched with the guard never seeing the
    destination. Each hop is validated before it is requested.
    """
    current = url

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as http_client:
        for _ in range(MAX_REDIRECTS + 1):
            await _assert_fetchable(current)
            response = await http_client.get(current)

            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("redirect with no Location header")
                # Relative Locations are legal; resolve against the
                # current url before validating.
                current = urljoin(current, location)
                continue

            response.raise_for_status()
            break
        else:
            raise ValueError(f"exceeded {MAX_REDIRECTS} redirects starting from {url[:100]!r}")

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    return soup.get_text(separator="\n", strip=True)

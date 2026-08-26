"""
Prompt for the GitHub agent's LLM filter stage.

This runs ONLY after a repo has already passed the rules filter
(stars_today >= 200). Scoring uses ONLY metadata already available
from the trending feed itself (name, description, language, star
counts) - deliberately NOT a README excerpt. Fetching anything from
GitHub's real API before this point would mean paying an API call
for every trending repo we look at, including the majority that
fail the filter - exactly the wasteful pattern the two-stage design
exists to avoid. The README is only ever fetched afterward, by
content_fetcher.py, for repos that already passed both stages.

JSON output is enforced via call_llm's forced tool-use (json_schema
below) - not by asking nicely in this prompt text, which is why
there's no "respond only in JSON" instruction here.
"""

GITHUB_FILTER_SYSTEM_PROMPT = """You are a technical evaluator for a tech intelligence \
system. You review GitHub repositories that have already shown strong star growth, and \
your job is to judge whether the repo is substantively interesting to a software \
engineer - not just popular.

Score the repo on three dimensions, each from 1 to 5:

novelty: Does this repo do something genuinely new or interesting, or is it a common \
project type (todo app, portfolio site, boilerplate template, course exercise)?

relevance: Is this relevant to AI engineering, backend systems, developer tooling, or \
broader software engineering practice? Repos far outside these areas score low even if \
they are popular.

applicability: Could a working engineer realistically use, learn from, or be affected by \
this repo's existence, based on its name and description? Purely academic or joke repos \
score low even if novel.

Base your judgment only on the repository's name, description, language, and star \
counts provided - you do not have access to its README or source code at this stage.
"""

GITHUB_FILTER_USER_TEMPLATE = """Repository: {repo_name}
Language: {language}
Stars gained today: {stars_today}
Total stars: {total_stars}
Description: {description}
"""


GITHUB_FILTER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "novelty": {"type": "integer", "minimum": 1, "maximum": 5},
        "relevance": {"type": "integer", "minimum": 1, "maximum": 5},
        "applicability": {"type": "integer", "minimum": 1, "maximum": 5},
        "justification": {"type": "string"},
    },
    "required": ["novelty", "relevance", "applicability", "justification"],
}
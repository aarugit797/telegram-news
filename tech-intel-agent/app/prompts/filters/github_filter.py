"""
Prompt for the GitHub agent's LLM filter stage.

This runs ONLY after a repo has already passed the rules filter
(stars_today >= 200). By the time this prompt runs, we already know
the repo has real momentum - this prompt's only job is judging QUALITY,
not popularity. Popularity was already checked by the rules engine.

We ask Claude to return strict JSON so our code can parse scores
programmatically without any fragile string-parsing logic.
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
this repo's existence? Purely academic or joke repos score low here even if novel.

Respond with ONLY valid JSON in this exact shape, nothing else before or after it:

{
  "novelty": <int 1-5>,
  "relevance": <int 1-5>,
  "applicability": <int 1-5>,
  "justification": "<one sentence explaining the scores>"
}
"""

GITHUB_FILTER_USER_TEMPLATE = """Repository: {repo_name}
Language: {language}
Stars today: {stars_today}
Total stars: {total_stars}
Description: {description}

README excerpt:
{readme_excerpt}
"""

# Matches call_llm's json_schema parameter - forces Claude to answer
# through a tool call shaped exactly like this, instead of us hoping
# free text happens to parse as JSON.
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
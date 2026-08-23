"""
STUB - to be built.

WHAT: System + user prompt for the HackerNews agent's LLM filter stage.

WHY: Same pattern as github_filter.py but the evaluation criteria
differ - this prompt must weigh COMMENT SENTIMENT and discussion
quality, since HN's real signal often lives in the comments, not
just the story title/score (which the rules engine already checked).

CONNECTS TO: Used by agents/hackernews_agent.py via filters/hybrid_filter.py.
"""

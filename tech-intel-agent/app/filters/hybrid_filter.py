from dataclasses import dataclass
from typing import Callable

from app.core.llm_client import call_llm

# The line every signal must clear on average across all 3 scores
# (novelty, relevance, applicability) to be considered send-worthy.
COMPOSITE_THRESHOLD = 3.5


@dataclass
class FilterResult:
    """
    stage_reached distinguishes WHERE a signal was rejected, not just
    whether it passed - directly needed later for the stats table
    (signals_passed_rules vs signals_passed_llm vs signals_sent),
    so we can see which stage is filtering too aggressively or too
    loosely, rather than only knowing a final yes/no.
    """
    passed: bool
    stage_reached: str   
    scores: dict | None = None
    composite_score: float | None = None
    justification: str | None = None


async def run_hybrid_filter(
    raw_data: dict,
    rules_check: Callable[[dict], bool],
    system_prompt: str,
    user_message: str,
    json_schema: dict,
    trace_name: str,
) -> FilterResult:
    """
    The shared two-stage filter all 5 agents call.

    rules_check: a function the CALLING agent defines and hands in -
    e.g. github_agent.py's own function checking stars_today >= 200.
    This file never hardcodes any source-specific threshold itself;
    it just calls whatever function it's given.

    Stage 1 (rules_check) runs first and costs nothing - if it
    returns False, we return immediately, no LLM call made at all.

    Stage 2 only runs on signals that passed stage 1 - sends
    system_prompt + user_message to Claude via call_llm, forced into
    json_schema's shape, and checks the average of the 3 returned
    scores against COMPOSITE_THRESHOLD.
    """
    if not rules_check(raw_data):
        return FilterResult(passed=False, stage_reached="rejected_by_rules")

    result = await call_llm(
        system_prompt=system_prompt,
        user_message=user_message,
        trace_name=trace_name,
        json_schema=json_schema,
        temperature=0.0,
        # Explicit, and the third place this has bitten after dedup and
        # the message composer. The default 1024 is a RESPONSE cap, and
        # on gemini-3.5-flash reasoning tokens are billed against it
        # while never appearing in the output - so the visible JSON gets
        # cut well before 1024 tokens of actual content.
        #
        # It became a live failure the moment filters gained a "summary"
        # field: two of nine repos in one run died with
        # "Unterminated string starting at: line 1 column ...", which is
        # truncation surfacing as a parse error rather than as "too
        # long". Both were dead-lettered instead of judged.
        #
        # This one call covers all five agents.
        max_tokens=4096,
    )

    scores = result.content
    composite = (scores["novelty"] + scores["relevance"] + scores["applicability"]) / 3

    if composite < COMPOSITE_THRESHOLD:
        return FilterResult(
            passed=False,
            stage_reached="rejected_by_llm",
            scores=scores,
            composite_score=composite,
            justification=scores.get("justification"),
        )

    return FilterResult(
        passed=True,
        stage_reached="passed",
        scores=scores,
        composite_score=composite,
        justification=scores.get("justification"),
    )

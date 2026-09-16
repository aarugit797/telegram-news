import re

from app.core.llm_client import call_llm
from app.core.logging_config import get_logger
from app.prompts.responder.query_rewriter import (
    QUERY_REWRITER_JSON_SCHEMA,
    QUERY_REWRITER_SYSTEM_PROMPT,
    QUERY_REWRITER_USER_TEMPLATE,
)

logger = get_logger(__name__)

# THE CHEAP GATE. Rewriting costs an LLM call on the path where a user is
# waiting, and most questions are already self-contained - "what's new
# with LLMs this week" needs no rewriting at all. Spending a call on
# every query to discover that would be the tail wagging the dog.
#
# WHY A WORD LIST RATHER THAN A MODEL. The thing that makes a question
# context-dependent is a referring expression with no antecedent in the
# sentence, and that is a closed class of words in English. Matching them
# is exact, free and instant, where a classifier would be a second model
# in front of the model we are trying to avoid calling.
#
# The list is deliberately tuned to over-trigger rather than under. A
# false positive costs one extra call and the rewriter returns the
# question unchanged; a false negative silently retrieves on a
# meaningless vector, which is the bug being fixed.
#
# "one" is included for "that one" / "the second one". "they" and "them"
# carry follow-ups about papers and teams. "same" catches "is the same
# thing true for...".
#
# TIME EXPRESSIONS ARE EXCLUDED. "what's new with LLMs this week" is
# completely self-contained, and "this week" / "these days" / "that
# morning" are common enough that letting them through would have spent
# a call on a large share of ordinary questions - which is exactly the
# waste the gate exists to prevent. "there" is left out entirely for the
# same reason: "are there any good vector databases" is the dominant use
# and refers to nothing.
#
# KNOWN AND ACCEPTED OVER-TRIGGER: relative "that", as in "any tools that
# run locally". Telling a relative pronoun from a demonstrative needs a
# parser, and the cost of being wrong here is one call that returns the
# question unchanged - the right side of the trade.
_TIME_NOUNS = r"week|month|year|morning|afternoon|evening|day|days|time|quarter"

_REFERRING = re.compile(
    r"\b(?:"
    rf"(?:that|this|these|those)\b(?!\s+(?:{_TIME_NOUNS})\b)"
    r"|it|its|it's|they|them|their|one|ones|same|above|former|latter"
    r")\b",
    re.IGNORECASE,
)


def needs_rewrite(question: str, context: str) -> bool:
    """
    Whether this question is worth spending a rewrite call on.

    Both conditions are required. No context means there is nothing to
    resolve a pronoun AGAINST, so the rewriter could only guess - and a
    guess is the outcome this whole step exists to avoid.
    """
    return bool(context.strip()) and bool(_REFERRING.search(question))


async def rewrite_for_retrieval(question: str, context: str) -> str:
    """
    Returns a search query with references resolved, or the original
    question when there is nothing to resolve or the referent is
    ambiguous.

    RETRIEVAL ONLY. Callers must keep answering the ORIGINAL question -
    the reader asked what they asked, and the rewrite is a search key.

    NEVER RAISES. This sits in front of retrieval on a live reply path,
    so a rewriter failure must degrade to the previous behaviour (search
    on the raw question) rather than lose the user's message. The raw
    question is a worse query, not a broken one.
    """
    if not needs_rewrite(question, context):
        logger.info(
            "Query rewrite skipped",
            extra={"extra_fields": {
                "original": question,
                "reason": "no referring expression" if context.strip() else "no context",
            }},
        )
        return question

    try:
        result = await call_llm(
            system_prompt=QUERY_REWRITER_SYSTEM_PROMPT,
            user_message=QUERY_REWRITER_USER_TEMPLATE.format(
                context=context, question=question
            ),
            trace_name="query-rewriter",
            json_schema=QUERY_REWRITER_JSON_SCHEMA,
            # Deterministic on purpose. This is a substitution, not
            # writing - the same question against the same history
            # should always produce the same search key, or an
            # unreproducible retrieval bug becomes impossible to chase.
            temperature=0.0,
            lane="responder",
        )
        rewritten = " ".join(str(result.content.get("query", "")).split())
        resolved = bool(result.content.get("resolved"))
    except Exception as e:
        logger.warning(
            "Query rewrite failed - retrieving on the original question",
            extra={"extra_fields": {
                "original": question, "error": str(e), "error_type": type(e).__name__,
            }},
        )
        return question

    # An empty query would retrieve nothing at all, which is worse than
    # retrieving badly.
    if not resolved or not rewritten:
        logger.info(
            "Query rewrite declined",
            extra={"extra_fields": {
                "original": question,
                "reason": "ambiguous referent" if not resolved else "empty rewrite",
            }},
        )
        return question

    logger.info(
        "Query rewritten for retrieval",
        extra={"extra_fields": {"original": question, "rewritten": rewritten}},
    )
    return rewritten

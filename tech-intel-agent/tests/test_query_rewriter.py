"""
Pins the cheap gate in front of the query rewriter.

The gate is what keeps a live reply path from spending an LLM call on
every search. It is pure, so it is worth testing hard - and its bias
matters: it should over-trigger rather than under, because a false
positive costs one call that returns the question unchanged, while a
false negative silently retrieves on a subjectless vector, which is the
bug the rewriter exists to fix.
"""
import pytest

from app.responder.query_rewriter import needs_rewrite

CONTEXT = "[outbound] VoiceStudio clones voices and runs locally."


@pytest.mark.parametrize("question", [
    "how do I install it?",
    "is that faster?",
    "what did they measure?",
    "tell me more about that",
    "is this one open source?",
    "are those benchmarks real",
    "does the same apply to Postgres",
])
def test_referring_expressions_trigger_a_rewrite(question):
    assert needs_rewrite(question, CONTEXT) is True


@pytest.mark.parametrize("question", [
    "what's new with LLMs this week",
    "any good open source vector databases",
    "who released a model yesterday",
    "explain retrieval augmented generation",
    "what happened with the Rust 1.90 release",
])
def test_self_contained_questions_skip_the_rewrite(question):
    """Most questions are already searchable. These must cost no call."""
    assert needs_rewrite(question, CONTEXT) is False


def test_no_context_means_no_rewrite():
    """
    With no history there is nothing to resolve a pronoun against, so
    the rewriter could only guess - and guessing is the outcome the
    whole step exists to avoid.
    """
    assert needs_rewrite("how do I install it?", "") is False
    assert needs_rewrite("how do I install it?", "   \n  ") is False


def test_matching_is_on_whole_words_only():
    """
    Substring matching would fire on almost everything - "it" lives
    inside "monitoring", "that" inside "thatch".
    """
    assert needs_rewrite("what is monitoring like in production", CONTEXT) is False
    assert needs_rewrite("explain transit gateway pricing", CONTEXT) is False


def test_case_is_ignored():
    assert needs_rewrite("How do I install IT?", CONTEXT) is True


@pytest.mark.parametrize("question", [
    "what's new with LLMs this week",
    "any interesting releases this month",
    "what shipped this year in Rust",
    "are there any good vector databases",
])
def test_time_expressions_and_existential_there_do_not_trigger(question):
    """
    "this week" is a demonstrative that refers to nothing needing
    resolution, and "are there any" is existential. Both are common
    enough that letting them through would spend a call on a large share
    of ordinary questions.
    """
    assert needs_rewrite(question, CONTEXT) is False


def test_relative_that_is_a_known_accepted_over_trigger():
    """
    Documents a deliberate limitation rather than asserting correctness.
    Distinguishing relative "that" from demonstrative "that" needs a
    parser; the cost of being wrong is one call that returns the
    question unchanged, which is the right side of the trade.
    """
    assert needs_rewrite("any tools that run locally", CONTEXT) is True

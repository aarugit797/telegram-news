"""
STUB - to be built.

WHAT: The single shared function every component uses to call Claude.
Wraps the raw Anthropic API call with automatic LangSmith tracing,
token/cost logging, and consistent error handling.

WHY: Without this, all ~14 components that call an LLM (5 filters,
dedup, urgency classifier, message composer, guardrail, intent
classifier, 4 responder tools, response composer) would each write
their own API call + tracing boilerplate. One shared function means
one place to fix bugs or change models.

INPUT: system_prompt (str), user_message (str), a name for the
LangSmith trace (e.g. "github-filter"), and optionally a flag for
whether structured JSON output is expected.

OUTPUT: The raw text or parsed JSON response from Claude, plus
token counts logged internally for cost tracking.

CONNECTS TO: Imported by every prompts/ consumer - agents,
filters/hybrid_filter.py, processor/*, responder/guardrail.py,
responder/intent_classifier.py, responder/tools/*, responder/response_composer.py.
"""

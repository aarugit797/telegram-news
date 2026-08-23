"""
STUB - to be built.

WHAT: Initializes LangSmith tracing and Sentry error monitoring once,
at process startup, for both the pipeline process and the responder
process.

WHY: We decided every LLM call across the system gets a named
LangSmith trace, and every unhandled exception gets captured by
Sentry. Rather than each file configuring this separately, it is
set up once here and imported.

INPUT: LangSmith API key and Sentry DSN from settings (app/core/config.py).

OUTPUT: Nothing returned - this has side effects (sets global tracing
context) that llm_client.py and FastAPI rely on being active.

CONNECTS TO: Called once in pipeline_main.py and once in responder/main.py,
at the very top before anything else runs.
"""

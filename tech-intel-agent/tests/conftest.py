"""
Test-wide configuration.

Keeps the suite from talking to real third-party services. Running pytest
was shipping events to the live Sentry project ("Sentry is attempting to
send 4 pending events" at the end of every run), which pollutes the error
dashboard with failures that were deliberately provoked by tests.

Why this is module-level code and not a fixture: tests/test_webhook.py
builds TestClient(app) at import time, and importing app.responder.main
runs init_observability() immediately, at import. Any fixture - even
session-scoped and autouse - runs too late, because collection has already
imported the test module and Sentry is already initialised against the
real DSN. pytest imports conftest.py before the test modules it covers, so
this is the last point where the environment can still be changed.

app.core.config builds its Settings singleton at import too, and
pydantic-settings gives environment variables precedence over values in
.env, so overriding them here is what wins.
"""

import os

# Blank rather than deleted: Settings declares sentry_dsn as a plain str
# defaulting to "", and init_observability() treats a falsy DSN as "not
# configured" and returns before sentry_sdk.init() is ever reached.
os.environ["SENTRY_DSN"] = ""

# LangSmith has no init step to intercept - its SDK reads these straight
# from the environment the first time trace() or wrap_anthropic() runs.
# Note init_observability() raises if tracing is on while the key is
# missing, so both have to move together.
os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["LANGCHAIN_API_KEY"] = ""

import pytest  # noqa: E402  - must come after the environment is set
import sentry_sdk  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def sentry_must_be_disabled():
    """
    Guard against regression. If someone later imports the app earlier than
    this conftest, or init_observability() stops honouring an empty DSN,
    the suite fails loudly here instead of quietly emitting events to the
    production Sentry project again.
    """
    client = sentry_sdk.get_client()
    assert not client.is_active(), (
        "Sentry is active during tests and would send real events. "
        f"Active client: {type(client).__name__}"
    )
    yield

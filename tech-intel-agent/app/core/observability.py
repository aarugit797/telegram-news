import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
import logging

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _validate_langsmith_config() -> None:
    """
    LangSmith needs no explicit client/init - its SDK reads
    LANGCHAIN_API_KEY / LANGCHAIN_TRACING_V2 / LANGCHAIN_PROJECT
    straight from environment variables the moment trace() or
    wrap_anthropic() is first used (see llm_client.py). Because
    there's no init step to fail loudly, a missing key would
    otherwise surface silently - every trace() call would just
    quietly fail deep inside a running agent, hours after startup,
    with no clear signal why. This function's only job is to catch
    that misconfiguration immediately, at process boot, instead.
    """
    if settings.langchain_tracing_v2 and not settings.langchain_api_key:
        raise RuntimeError(
            "LANGCHAIN_TRACING_V2 is enabled but LANGCHAIN_API_KEY is missing. "
            "Every LLM call's LangSmith trace would silently fail - fix this before starting."
        )


def init_observability(service_name: str, include_fastapi: bool = False) -> None:
    """
    Called once, at the very top of each process's startup -
    pipeline_main.py calls init_observability("intelligence-pipeline"),
    responder/main.py calls
    init_observability("whatsapp-responder", include_fastapi=True).

    service_name: tags every Sentry event so errors from the two
    independent processes are distinguishable in the dashboard,
    rather than all appearing to come from one undifferentiated app.

    include_fastapi: only the responder process actually runs
    FastAPI - the intelligence pipeline has no web framework at all,
    so it would be wrong (and would error) to include FastAPI-specific
    integrations there.
    """
    _validate_langsmith_config()

    if not settings.sentry_dsn:
        logger.info("SENTRY_DSN not set - Sentry error monitoring is disabled for this run.")
        return

    integrations = [
        # LoggingIntegration is technically a DEFAULT integration -
        # Sentry enables it automatically even if we didn't list it
        # explicitly. We list it anyway, with explicit thresholds,
        # because the defaults (level=INFO as breadcrumb, event_level=
        # ERROR as a real alert) are close to what we want, but being
        # explicit here means this behavior is visible in code, not
        # hidden in an SDK default someone has to go look up.
        LoggingIntegration(
            level=logging.WARNING,       # WARNING+ becomes breadcrumb context on a later event
            event_level=logging.ERROR,   # ERROR+ becomes an actual Sentry alert
        ),
    ]

    if include_fastapi:
        # FastAPI is built on Starlette, so Sentry's own docs are
        # explicit that both integrations are required together -
        # FastApiIntegration alone is not sufficient.
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        integrations += [StarletteIntegration(), FastApiIntegration()]

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        integrations=integrations,
        traces_sample_rate=1.0 if settings.environment == "local" else 0.2,
        # We handle real WhatsApp numbers and message content - never
        # send that to a third-party service by default. This is a
        # deliberate privacy choice, not an oversight of the default.
        send_default_pii=False,
    )

    logger.info(
        f"Observability initialized for {service_name}",
        extra={"extra_fields": {"environment": settings.environment, "fastapi": include_fastapi}},
    )

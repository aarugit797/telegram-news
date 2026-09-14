from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central place where every piece of config lives.
    Pydantic reads matching values from your .env file automatically
    and validates their types - if DATABASE_URL is missing, the app
    will refuse to start with a clear error instead of failing later
    with a confusing crash.
    """

    database_url: str                    # News DB
    conversation_database_url: str       # Conversation DB - separate database entirely
    redis_url: str
    # Comma-separated, one key per Google Cloud project. Kept as a raw
    # string rather than list[str] because pydantic-settings JSON-decodes
    # complex types from env vars before any validator runs, so "a,b,c"
    # would raise SettingsError. gemini_keys() does the split.
    #
    # A list rather than numbered settings so the count changes without a
    # code change - adding a fourth project is an .env edit.
    gemini_api_keys: str = ""
    groq_api_key: str = ""

    # Google AI Studio free tier. Taken from models.list rather than
    # assumed, then picked on measured availability: gemini-3.8-flash
    # is newer but returned 503 UNAVAILABLE ("high demand") on 1 of 3
    # structured calls, while 3.5-flash served 3 of 3. An agent run
    # that dies partway through on a transient 503 is worse than one
    # using a slightly older model.
    #
    # Deliberately NOT the "gemini-flash-latest" alias: this model
    # scores signals against fixed numeric thresholds
    # (hybrid_filter.COMPOSITE_THRESHOLD), so a model changing
    # underneath us would shift those scores with no code change and no
    # way to attribute the drift.
    default_llm_model: str = "gemini-3.5-flash"
    llm_timeout_seconds: float = 20.0

    # ---- LLM rate limiting -------------------------------------------------
    # The constraint is SYSTEM-WIDE, not per-agent. Gemini's free tier is
    # ~10-15 RPM shared across everything, and the pipeline and responder
    # are separate OS processes - an in-process asyncio throttle in each
    # would let both independently throttle to the limit and together
    # exceed it. These budgets are enforced in Redis for that reason.

    # Held below the stated ~15 RPM for headroom: the ceiling is not
    # documented precisely and a 429 costs more than a slightly slower run.
    llm_rpm_total: int = 10
    # Slots the pipeline may never take, so a cold start (~75 calls in
    # minutes) or arXiv's ~25-call daily burst cannot starve a user
    # waiting on a WhatsApp reply. Pipeline ceiling is total - reserved.
    #
    # Applied PER CREDENTIAL now, not once globally: with a pool of
    # credentials each carrying its own quota, one global reservation
    # would leave the responder crowded out on whichever credential the
    # pipeline happened to pick.
    llm_rpm_responder_reserved: int = 2

    # Requests per day, same split. Verify against the specific model's
    # documented free-tier RPD - it differs per model and is not
    # discoverable from the API.
    llm_rpd_total: int = 1000
    # The responder alone is ~500 calls/day at moderate use, more than the
    # whole pipeline. Capping background work at 40% leaves 600 for it.
    llm_rpd_pipeline_fraction: float = 0.4

    # ---- per-user daily allowance ----------------------------------------
    # Quota, not dollars - there is no per-call price on a free tier. The
    # scarce resource is the ~3,000 requests/day shared across every
    # credential, of which the responder alone can take ~500.
    #
    # Both are enforced because they bind at different times: many short
    # messages hit the request cap first, while a long conversation -
    # where each turn carries a growing history - hits the token cap with
    # a modest request count.
    user_daily_request_limit: int = 40
    user_daily_token_limit: int = 60_000

    # ---- per-credential quotas -------------------------------------------
    # Measured, not assumed: one key served 6 calls then returned 429, so
    # the real ceiling is ~5-6 RPM per project rather than the 10-15 the
    # docs imply. Held at 5 for headroom.
    gemini_rpm_per_key: int = 5
    gemini_rpd_per_key: int = 250

    # Groq is OVERFLOW, not a peer. Its RPM is generous but a 100K
    # tokens-per-day cap puts the real ceiling near 90 calls/day at our
    # prompt sizes, so selection prefers Gemini and only falls through to
    # this once every Gemini credential is spent.
    groq_rpm: int = 30
    groq_rpd: int = 90
    groq_model: str = "openai/gpt-oss-20b"

    # How long a credential is parked after a 429 when the response
    # carries no retry-after hint of its own.
    llm_cooldown_seconds: int = 60

    # Backoff attempts for TRANSIENT failures (429/503). Deliberately
    # separate from call_llm's max_retries, which covers malformed JSON -
    # a different failure class that should not share a budget.
    llm_max_transient_retries: int = 4
    llm_backoff_base_seconds: float = 1.0
    llm_backoff_max_seconds: float = 32.0
    # How long a caller waits for an RPM slot before giving up and raising
    # LLMQuotaExhausted.
    llm_slot_wait_seconds: float = 45.0

    # ---- messaging channel ------------------------------------------------
    # Which service actually carries messages. Telegram by default because
    # Twilio's WhatsApp sandbox cannot send LLM-written text on a trial
    # account (21654 ContentSid Required - business-initiated messages
    # need pre-approved templates). Set to "whatsapp" once a paid account
    # and an approved WABA exist; no code change is needed.
    active_channel: str = "telegram"
    telegram_bot_token: str = ""

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""

    voyage_api_key: str = ""

    tavily_api_key: str = ""

    github_token: str = ""
    # GitHub's own trending page. The previous value here was a
    # third-party JSON wrapper (api.gitterapp.com) which now 404s on
    # every path - see _fetch_trending_repos for why we scrape HTML.
    github_trending_base: str = "https://github.com/trending"

    
    langchain_api_key: str = ""
    langchain_tracing_v2: bool = False
    langchain_project: str = "tech-intel-agent"
    sentry_dsn: str = ""
    environment: str = "local"

    # The timezone every CronTrigger in agents/scheduler.py is anchored
    # to. Without an explicit value APScheduler falls back to the
    # SERVER's local time, so "8am" would mean one thing on a laptop
    # and another on an EC2 box running UTC - the jobs would silently
    # fire at different real-world times per deployment.
    scheduler_timezone: str = "Asia/Kolkata"

    def gemini_keys(self) -> list[str]:
        """Parsed GEMINI_API_KEYS - order preserved, blanks and duplicates dropped."""
        seen, out = set(), []
        for raw in self.gemini_api_keys.split(","):
            key = raw.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
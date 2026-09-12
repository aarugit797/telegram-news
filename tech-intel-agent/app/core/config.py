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
    anthropic_api_key: str


    default_llm_model: str = "claude-haiku-4-5-20251001"
    llm_timeout_seconds: float = 20.0

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
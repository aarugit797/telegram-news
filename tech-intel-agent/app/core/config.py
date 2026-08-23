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

    # Used by the responder + Twilio sender
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""

    # Embeddings (News DB Tool + agents writing signals)
    voyage_api_key: str = ""

    # Web search tool
    tavily_api_key: str = ""

    # Observability
    langchain_api_key: str = ""
    langchain_tracing_v2: bool = False
    langchain_project: str = "tech-intel-agent"
    sentry_dsn: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Created once, imported everywhere else in the app.
# This is a common pattern called a "singleton config object".
settings = Settings()

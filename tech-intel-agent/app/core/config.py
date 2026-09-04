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
    github_trending_api_base: str = "https://api.gitterapp.com/repositories"
    ARXIV_API_URL = "http://export.arxiv.org/api/query"

    
    langchain_api_key: str = ""
    langchain_tracing_v2: bool = False
    langchain_project: str = "tech-intel-agent"
    sentry_dsn: str = ""
    environment: str = "local"   

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
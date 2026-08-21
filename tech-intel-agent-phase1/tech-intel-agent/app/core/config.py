from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central place where every piece of config lives.
    Pydantic reads matching values from your .env file automatically
    and validates their types - if DATABASE_URL is missing, the app
    will refuse to start with a clear error instead of failing later
    with a confusing crash.
    """

    database_url: str
    redis_url: str
    anthropic_api_key: str

    # Added now, used later when we build the responder + Twilio sender
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Created once, imported everywhere else in the app.
# This is a common pattern called a "singleton config object".
settings = Settings()
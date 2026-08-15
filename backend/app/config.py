"""
config.py
---------
Centralised settings loader using Pydantic BaseSettings.
Import `settings` anywhere in the app to access typed configuration values.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings — populated from .env file or environment variables.
    Pydantic validates types automatically at startup.
    """

    # Groq Cloud LLM
    groq_api_key: str

    # Supabase
    supabase_url: str
    supabase_key: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Singleton — import this object everywhere
settings = Settings()

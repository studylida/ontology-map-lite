from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ONTOLOGY_MAP_",
        extra="ignore",
    )

    database_url: PostgresDsn
    environment: Literal["development", "test", "production"]

    # API Keys (Loaded securely via dotenv or OS environment)
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()

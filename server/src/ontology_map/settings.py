from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ONTOLOGY_MAP_",
        extra="ignore",
    )

    database_url: PostgresDsn
    environment: Literal["development", "test", "production"]
    source_processing_enabled: bool = False
    demo_unbounded_provider: bool = False
    provider_ledger_dir: Path | None = None
    source_processing_workers: int = Field(default=4, ge=1, le=4)
    provider_min_interval_seconds: float = Field(default=0.13, gt=0)

    @model_validator(mode="after")
    def validate_source_processing(self) -> "Settings":
        if self.source_processing_enabled and not self.demo_unbounded_provider:
            raise ValueError("SOURCE_PROCESSING_REQUIRES_DEMO_PROVIDER_APPROVAL")
        if self.source_processing_enabled and (
            self.provider_ledger_dir is None
            or not self.provider_ledger_dir.is_absolute()
        ):
            raise ValueError("SOURCE_PROCESSING_LEDGER_DIR_REQUIRED")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

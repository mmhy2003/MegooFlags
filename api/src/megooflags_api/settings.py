from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEGOOFLAGS_", extra="ignore")

    database_url: str = "postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags"
    db_connect_timeout: float = 2.0
    public_url: AnyHttpUrl | None = None
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    valkey_url: str = "redis://localhost:6379/0"
    persist_context: bool = False
    exposure_retention_days: int = Field(30, ge=1)
    client_rate_limit_per_minute: int = Field(600, ge=1)
    sse_heartbeat_seconds: float = Field(15, gt=0)
    stream_recheck_seconds: float = Field(30, gt=0)
    api_workers: int = Field(0, ge=0)  # 0: one per CPU, at most serve.MAX_AUTO_WORKERS

    @field_validator("public_url", mode="before")
    @classmethod
    def _empty_public_url_is_none(cls, value: object) -> object:
        # docker-compose passes MEGOOFLAGS_PUBLIC_URL="" when it is not configured.
        return None if value == "" else value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()

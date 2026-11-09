from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEGOOFLAGS_", extra="ignore")

    database_url: str = "postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags"
    db_connect_timeout: float = 2.0
    public_url: AnyHttpUrl | None = None
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

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

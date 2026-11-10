from pydantic import AwareDatetime, Field, JsonValue

from megooflags_api.schemas.common import ApiModel

MAX_BATCH = 1000


class ExposureIn(ApiModel):
    flag_key: str = Field(min_length=1, max_length=200)
    variant: str | None = Field(None, max_length=200)
    reason: str = Field(min_length=1, max_length=50)
    timestamp: AwareDatetime = Field(description="Clamped to the time the batch is received.")
    context: dict[str, JsonValue] | None = Field(
        None, description="Stored only when `MEGOOFLAGS_PERSIST_CONTEXT` is enabled."
    )


class CountIn(ApiModel):
    flag_key: str = Field(min_length=1, max_length=200)
    variant: str | None = Field(None, max_length=200)
    hour: AwareDatetime = Field(description="Truncated to the hour.")
    count: int = Field(ge=1, le=1_000_000_000)


class EventsIn(ApiModel):
    exposures: list[ExposureIn] = Field(default_factory=list, max_length=MAX_BATCH)
    counts: list[CountIn] = Field(default_factory=list, max_length=MAX_BATCH)

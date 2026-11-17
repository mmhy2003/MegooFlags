import uuid
from typing import Literal

from pydantic import Field

from megooflags_api.schemas.common import ApiModel
from megooflags_api.schemas.events import MAX_BATCH, CountIn, ExposureIn

MAX_SNAPSHOT_AGE = 1e9  # about 31 years, so the age always fits a timedelta


class StreamIn(ApiModel):
    versions: dict[uuid.UUID, int] = Field(
        default_factory=dict, description="The snapshot version the worker holds per environment."
    )
    public_key: str | None = Field(
        None,
        max_length=100,
        description="The key the worker verifies with; when it differs, every snapshot is sent.",
    )


class HeartbeatIn(ApiModel):
    version: str = Field(max_length=50)
    snapshot_version: int = Field(ge=0)
    snapshot_age_seconds: float | None = Field(
        None,
        ge=0,
        le=MAX_SNAPSHOT_AGE,
        description="Seconds since the worker last synced; None before its first sync.",
    )
    sdk_streams: int = Field(ge=0)
    buffered_events: int = Field(ge=0)
    ready: bool


class HeartbeatOut(ApiModel):
    state: Literal["active", "draining"]


class ClusterEventsIn(ApiModel):
    batch_id: uuid.UUID = Field(description="Fixed by the worker; a batch seen before is ignored.")
    environment_id: uuid.UUID
    exposures: list[ExposureIn] = Field(default_factory=list, max_length=MAX_BATCH)
    counts: list[CountIn] = Field(default_factory=list, max_length=MAX_BATCH)

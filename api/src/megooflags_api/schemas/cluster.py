import uuid
from typing import Literal

from pydantic import Field

from megooflags_api.schemas.common import ApiModel
from megooflags_api.schemas.events import MAX_BATCH, CountIn, ExposureIn


class HeartbeatIn(ApiModel):
    version: str = Field(max_length=50)
    snapshot_version: int = Field(ge=0)
    snapshot_age_seconds: float | None = Field(
        None, ge=0, description="Seconds since the worker last synced; None before its first sync."
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

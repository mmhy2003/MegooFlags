import uuid
from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from megooflags_api.models.enums import ScheduledAction, ScheduleStatus
from megooflags_api.schemas.common import ApiModel
from megooflags_api.schemas.targeting import WeightIn


class ScheduleIn(ApiModel):
    """A change to apply at `run_at`, which must be in the future and at most 365 days ahead.

    `weights` (the new fallthrough rollout) is required for set_rollout and refused otherwise.
    """

    action: ScheduledAction
    run_at: AwareDatetime
    weights: list[WeightIn] | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def _weights_match_the_action(self) -> Self:
        if self.action == ScheduledAction.SET_ROLLOUT and self.weights is None:
            raise ValueError("weights are required for set_rollout")
        if self.action != ScheduledAction.SET_ROLLOUT and self.weights is not None:
            raise ValueError(f"weights are not allowed for {self.action.value}")
        return self


class ScheduleOut(ApiModel):
    id: uuid.UUID
    environment: str
    action: ScheduledAction
    weights: list[WeightIn] | None
    run_at: datetime
    status: ScheduleStatus
    error: str | None
    created_by: str
    change_request_id: uuid.UUID | None
    created_at: datetime
    executed_at: datetime | None

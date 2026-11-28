import uuid
from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from megooflags_api.models.enums import RolloutStatus
from megooflags_api.schemas.common import ApiModel, Key
from megooflags_api.schemas.targeting import WeightIn

FULL = 100000


class RolloutStepIn(ApiModel):
    """The "to" variation's share in thousandths of a percent, then the wait in minutes before
    the next step (ignored on the last step)."""

    percentage: int = Field(ge=1, le=FULL)
    wait_minutes: int = Field(60, ge=1, le=43200)


class ProgressiveRolloutIn(ApiModel):
    """Moves the fallthrough from `from_variation` to `to_variation` in steps whose percentages
    strictly increase and end at 100000."""

    from_variation: Key
    to_variation: Key
    steps: list[RolloutStepIn] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _steps_go_up_to_full(self) -> Self:
        if self.from_variation == self.to_variation:
            raise ValueError("from_variation and to_variation must differ")
        percentages = [step.percentage for step in self.steps]
        if any(a >= b for a, b in zip(percentages, percentages[1:], strict=False)):
            raise ValueError("step percentages must strictly increase")
        if percentages[-1] != FULL:
            raise ValueError(f"the last step must be {FULL}")
        return self


class ProgressiveRolloutOut(ApiModel):
    """`from_variation` or `to_variation` is null once that variation is deleted."""

    id: uuid.UUID
    environment: str
    from_variation: str | None
    to_variation: str | None
    steps: list[RolloutStepIn]
    current_step: int
    next_step_at: datetime | None
    remaining_seconds: int | None
    status: RolloutStatus
    last_applied_weights: list[WeightIn] | None
    error: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class RolloutView(ApiModel):
    """The live (active or paused) rollout, if any, and the 10 most recent finished ones."""

    current: ProgressiveRolloutOut | None
    recent: list[ProgressiveRolloutOut]

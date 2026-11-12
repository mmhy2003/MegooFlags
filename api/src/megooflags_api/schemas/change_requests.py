import uuid
from datetime import datetime

from pydantic import Field, JsonValue

from megooflags_api.models.enums import ChangeRequestKind, ChangeRequestStatus, ReviewDecision
from megooflags_api.schemas.common import ApiModel, Description, Key, Name
from megooflags_api.schemas.targeting import FlagConfigIn, FlagConfigOut


class ChangeRequestCreate(ApiModel):
    """A change to `flag` in `environment` that waits for approval.

    `payload` is the body of the direct endpoint: FlagConfigIn for config, ToggleIn for toggle,
    ScheduleIn for schedule and ProgressiveRolloutIn for rollout. It is checked by a dry run, so
    an invalid change is refused now; creating needs the permission of the direct endpoint.
    """

    environment: Key
    flag: Key
    kind: ChangeRequestKind
    title: Name
    description: Description = ""
    payload: dict[str, JsonValue]


class CommentIn(ApiModel):
    body: str = Field(min_length=1, max_length=10000)


class ReviewOut(ApiModel):
    reviewer: str
    decision: ReviewDecision
    comment: str | None
    created_at: datetime


class CommentOut(ApiModel):
    id: uuid.UUID
    author: str
    body: str
    created_at: datetime


class ChangeRequestSummary(ApiModel):
    """`approvals` counts the approving reviews; `approvals_required` is the environment's
    current setting, at least 1."""

    id: uuid.UUID
    environment: str
    flag: str
    kind: ChangeRequestKind
    title: str
    status: ChangeRequestStatus
    author: str
    approvals: int
    approvals_required: int
    created_at: datetime
    closed_at: datetime | None


class ChangeRequestOut(ChangeRequestSummary):
    """`current` (the live config) and `proposed` are set for config change requests only."""

    description: str
    payload: dict[str, JsonValue]
    base_version: int
    error: str | None
    reviews: list[ReviewOut]
    comments: list[CommentOut]
    current: FlagConfigOut | None
    proposed: FlagConfigIn | None

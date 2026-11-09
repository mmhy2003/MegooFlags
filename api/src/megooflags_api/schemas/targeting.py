from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

from megooflags_api.models.enums import Operator
from megooflags_api.schemas.common import ApiModel, Description, Key

Scalar = StrictStr | StrictInt | StrictFloat | StrictBool
ContextKey = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class ClauseIn(ApiModel):
    attribute: str = Field("key", min_length=1, max_length=200)
    operator: Operator
    values: list[Scalar] = Field(min_length=1, max_length=1000)
    negate: bool = False


class ClauseOut(ApiModel):
    """For `in_segment`, `values` are the segment keys, sorted."""

    attribute: str
    operator: Operator
    values: list[Scalar]
    negate: bool


class WeightIn(ApiModel):
    """A variation's share in thousandths of a percent; a rollout's weights add up to 100000."""

    variation: Key
    weight: int = Field(ge=0, le=100000)


class RolloutIn(ApiModel):
    bucket_by: str = Field("key", min_length=1, max_length=200)
    weights: list[WeightIn] = Field(min_length=1)


class Serve(ApiModel):
    """What a rule or the fallthrough serves: exactly one of a fixed variation or a rollout."""

    variation: Key | None = None
    rollout: RolloutIn | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.variation is None) == (self.rollout is None):
            raise ValueError("set exactly one of variation or rollout")
        return self


class RuleIn(Serve):
    description: Description = ""
    clauses: list[ClauseIn] = Field(min_length=1)


class RuleOut(Serve):
    description: str
    clauses: list[ClauseOut]


class TargetIn(ApiModel):
    """Context keys served `variation`; a key appears in one list only."""

    variation: Key
    values: list[ContextKey] = Field(min_length=1)


class PrerequisiteIn(ApiModel):
    """The prerequisite `flag` must serve `variation` for this flag to be evaluated."""

    flag: Key
    variation: Key


class FlagConfigIn(ApiModel):
    version: int
    enabled: bool
    targets: list[TargetIn] = []
    rules: list[RuleIn] = []
    fallthrough: Serve
    prerequisites: list[PrerequisiteIn] = []


class ToggleIn(ApiModel):
    """When `version` is set it must match the current one, or 409."""

    enabled: bool
    version: int | None = None


class CopyIn(ApiModel):
    """Copy the config of environment `from` to every environment in `to`.

    The on/off state is copied only with `include_enabled`.
    """

    source: Key = Field(alias="from")
    to: list[Key] = Field(min_length=1)
    include_enabled: bool = False


class FlagConfigOut(ApiModel):
    """Targets are grouped per variation in variation order, with values sorted."""

    environment: str
    enabled: bool
    version: int
    targets: list[TargetIn]
    rules: list[RuleOut]
    fallthrough: Serve
    prerequisites: list[PrerequisiteIn]
    updated_at: datetime

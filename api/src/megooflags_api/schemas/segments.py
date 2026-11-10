from datetime import datetime

from pydantic import Field

from megooflags_api.schemas.common import ApiModel, Description, Key, Name
from megooflags_api.schemas.targeting import ClauseIn, ClauseOut, ContextKey


class SegmentCreate(ApiModel):
    key: Key
    name: Name
    description: Description = ""


class SegmentUpdate(ApiModel):
    name: Name | None = None
    description: Description | None = None


class SegmentRuleIn(ApiModel):
    description: Description = ""
    clauses: list[ClauseIn] = Field(min_length=1)


class SegmentRuleOut(ApiModel):
    description: str
    clauses: list[ClauseOut]


class SegmentConfigIn(ApiModel):
    """Replaces the whole config. A context key may be in one list only; repeats are dropped."""

    included: list[ContextKey] = []
    excluded: list[ContextKey] = []
    rules: list[SegmentRuleIn] = []


class SegmentSummary(ApiModel):
    key: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


class SegmentOut(SegmentSummary):
    """`included` and `excluded` are sorted; `rules` keep their order."""

    included: list[str]
    excluded: list[str]
    rules: list[SegmentRuleOut]

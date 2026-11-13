from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue

from megooflags_api.schemas.common import ApiModel


class EvaluateIn(ApiModel):
    context: dict[str, JsonValue]
    flags: list[str] | None = Field(
        None, description="Flag keys to evaluate; every non-archived flag when omitted."
    )


class EvaluationResult(ApiModel):
    """An OpenFeature resolution detail. `value` is null when the flag is not found."""

    key: str
    value: JsonValue
    variant: str | None
    reason: str
    error_code: str | None
    error_message: str | None
    metadata: dict[str, JsonValue]


class EvaluateOut(ApiModel):
    results: list[EvaluationResult]


class EvaluationPoint(ApiModel):
    start: datetime = Field(description="The UTC start of the hour or day.")
    counts: dict[str, int] = Field(description="Evaluations per variant; 0 for every variant.")


class EvaluationSeries(ApiModel):
    """A flag's evaluations in one environment. Variant "" counts evaluations with no variant."""

    interval: Literal["hour", "day"]
    variants: list[str] = Field(description="Every variant counted in the range, sorted.")
    points: list[EvaluationPoint] = Field(description="Oldest first, empty buckets included.")
    total: int

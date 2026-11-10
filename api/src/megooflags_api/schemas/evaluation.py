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

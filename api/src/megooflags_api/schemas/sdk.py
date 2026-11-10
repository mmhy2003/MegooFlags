from pydantic import Field, JsonValue

from megooflags_api.schemas.common import ApiModel


class SdkEvaluationResult(ApiModel):
    """An OpenFeature resolution detail. `value` is null when the flag is not found."""

    key: str
    value: JsonValue
    variant: str | None
    reason: str
    error_code: str | None
    error_message: str | None
    metadata: dict[str, JsonValue] | None = Field(None, description="Server keys only.")


class SdkEvaluateOut(ApiModel):
    results: list[SdkEvaluationResult]

"""OpenFeature Remote Evaluation Protocol (OFREP) bodies. Field names are camelCase, as the
protocol requires, so these are plain models rather than `ApiModel`."""

from pydantic import BaseModel, Field, JsonValue


class OfrepRequest(BaseModel):
    """Unknown fields are ignored, so newer providers keep working."""

    context: dict[str, JsonValue] = Field(default_factory=dict)


class OfrepSuccess(BaseModel):
    key: str
    value: JsonValue
    reason: str
    variant: str | None = None
    metadata: dict[str, JsonValue] = Field(description="Empty for client keys.")


class OfrepError(BaseModel):
    key: str
    errorCode: str
    errorDetails: str | None = None


class OfrepBulk(BaseModel):
    flags: list[OfrepSuccess | OfrepError]

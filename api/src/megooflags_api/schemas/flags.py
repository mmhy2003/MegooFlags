from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StringConstraints, field_validator

from megooflags_api.models.enums import FlagType
from megooflags_api.schemas.common import ApiModel, Description, FlagEnvironmentState, Key, Name

Tag = Annotated[str, StringConstraints(min_length=1, max_length=64)]

# The stored states plus "archived", which comes from `archived_at`.
LifecycleState = Literal["new", "active", "permanent", "stale", "archived"]


def _dedupe(tags: list[str] | None) -> list[str] | None:
    """Drop repeated tags, keeping the first occurrence's position."""
    return None if tags is None else list(dict.fromkeys(tags))


class VariationIn(ApiModel):
    key: Key
    name: str = Field("", max_length=200)
    description: Description = ""
    value: JsonValue


class VariationUpdate(ApiModel):
    name: str | None = Field(None, max_length=200)
    description: Description | None = None
    # null is a valid JSON flag value: check `model_fields_set` to tell it from an absent value.
    value: JsonValue = None


class VariationOut(ApiModel):
    key: str
    name: str
    description: str
    value: JsonValue


class FlagCreate(ApiModel):
    key: Key
    name: Name
    description: Description = ""
    type: FlagType
    tags: list[Tag] = Field([], max_length=50)
    owner: str | None = Field(None, max_length=200)
    variations: list[VariationIn] | None = Field(
        None,
        description="Required for string, number and JSON flags. "
        "Boolean flags always get the variations true and false.",
    )
    off_variation: Key | None = Field(
        None, description="Key of the variation served when the flag is off."
    )

    _dedupe_tags = field_validator("tags")(_dedupe)


class FlagUpdate(ApiModel):
    name: Name | None = None
    description: Description | None = None
    tags: list[Tag] | None = Field(None, max_length=50)
    owner: str | None = Field(None, max_length=200, description="Send null to clear the owner.")
    off_variation: Key | None = None

    _dedupe_tags = field_validator("tags")(_dedupe)


class LifecycleIn(ApiModel):
    permanent: bool = Field(
        description="Permanent flags are never stale. Clearing the mark gives active or new."
    )


class FlagSummary(ApiModel):
    key: str
    name: str
    description: str
    type: FlagType
    tags: list[str]
    owner: str | None
    archived: bool
    archived_at: datetime | None
    lifecycle: LifecycleState
    last_evaluated_at: datetime | None
    environments: list[FlagEnvironmentState]
    created_at: datetime
    updated_at: datetime


class FlagOut(FlagSummary):
    off_variation: str
    variations: list[VariationOut]

from datetime import datetime
from typing import Annotated

from pydantic import Field

from megooflags_api.schemas.common import ApiModel, Description, Key, Name


class EnvironmentCreate(ApiModel):
    key: Key
    name: Name
    description: Description = ""
    sort_order: int | None = None


class EnvironmentUpdate(ApiModel):
    name: Name | None = None
    description: Description | None = None
    sort_order: int | None = None


class EnvironmentOut(ApiModel):
    key: str
    name: str
    description: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


class ProjectCreate(ApiModel):
    key: Key
    name: Name
    description: Description = ""
    environments: list[EnvironmentCreate] | None = Field(
        None,
        min_length=1,
        description="Defaults to development, staging and production when omitted.",
    )


StaleAfterDays = Annotated[int, Field(ge=1, le=365)]


class ProjectUpdate(ApiModel):
    name: Name | None = None
    description: Description | None = None
    stale_after_days: StaleAfterDays | None = Field(
        None, description="A flag no environment has evaluated for this many days is stale."
    )


class ProjectSummary(ApiModel):
    key: str
    name: str
    description: str
    stale_after_days: int
    created_at: datetime
    updated_at: datetime


class ProjectOut(ProjectSummary):
    environments: list[EnvironmentOut]

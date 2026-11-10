from datetime import datetime

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


class ProjectUpdate(ApiModel):
    name: Name | None = None
    description: Description | None = None


class ProjectSummary(ApiModel):
    key: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime


class ProjectOut(ProjectSummary):
    environments: list[EnvironmentOut]

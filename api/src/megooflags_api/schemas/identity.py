import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import Field, model_validator

from megooflags_api.auth.rbac import Permission
from megooflags_api.models.enums import ScopeKind, SubjectKind, UserStatus
from megooflags_api.schemas.common import ApiModel, Description, Key, Name


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    name: str
    status: UserStatus
    has_password: bool
    created_at: datetime
    last_login_at: datetime | None


class RoleIn(ApiModel):
    key: Key
    name: Name
    description: Description = ""
    permissions: Annotated[list[Permission], Field(min_length=1)]


class RoleUpdate(ApiModel):
    name: Name | None = None
    description: Description | None = None
    permissions: Annotated[list[Permission], Field(min_length=1)] | None = None


class RoleOut(ApiModel):
    id: uuid.UUID
    key: str
    name: str
    description: str
    builtin: bool
    permissions: list[Permission]


class ScopeFields(ApiModel):
    """A scope: `org`, or `project` with `project`, or `environment` with `project` and `env`."""

    scope: ScopeKind
    project: Key | None = None
    env: Key | None = None

    @model_validator(mode="after")
    def _fields_match_scope(self) -> Self:
        needs_project = self.scope != ScopeKind.ORG
        needs_env = self.scope == ScopeKind.ENVIRONMENT
        if (self.project is not None) != needs_project:
            raise ValueError(
                f"'project' is {'required' if needs_project else 'not allowed'}"
                f" at {self.scope} scope"
            )
        if (self.env is not None) != needs_env:
            raise ValueError(
                f"'env' is {'required' if needs_env else 'not allowed'} at {self.scope} scope"
            )
        return self


class BindingIn(ScopeFields):
    subject_kind: SubjectKind
    subject_id: uuid.UUID
    role: Key


class BindingOut(ApiModel):
    id: uuid.UUID
    subject_kind: SubjectKind
    subject_id: uuid.UUID
    # The user's email, the team's key or the token's name; None once the subject is gone.
    subject_label: str | None
    role: str
    scope: ScopeKind
    project: str | None
    env: str | None
    source: str
    created_at: datetime

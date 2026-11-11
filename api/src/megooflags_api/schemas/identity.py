import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import Field, model_validator

from megooflags_api.auth.rbac import Permission
from megooflags_api.models.enums import InvitationPurpose, ScopeKind, SubjectKind, UserStatus
from megooflags_api.schemas.auth import Password
from megooflags_api.schemas.common import ApiModel, Description, Email, Key, Name


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    name: str
    status: UserStatus
    has_password: bool
    created_at: datetime
    last_login_at: datetime | None


class UserCreate(ApiModel):
    email: Email
    name: Name


class UserUpdate(ApiModel):
    name: Name | None = None


class InviteLink(ApiModel):
    """A one-time link to set a password, shown once; the admin shares it with the user."""

    url: str
    expires_at: datetime


class UserCreated(ApiModel):
    user: UserOut
    invite: InviteLink


class InvitationOut(ApiModel):
    email: str
    name: str
    purpose: InvitationPurpose
    expires_at: datetime


class InvitationAccept(ApiModel):
    password: Password


class TeamIn(ApiModel):
    key: Key
    name: Name


class TeamUpdate(ApiModel):
    name: Name | None = None


class TeamOut(ApiModel):
    id: uuid.UUID
    key: str
    name: str
    created_at: datetime
    member_count: int


class TeamMemberIn(ApiModel):
    user_id: uuid.UUID


class TeamMemberOut(ApiModel):
    user: UserOut
    # "manual", or "sso:<provider key>" for memberships managed by group sync.
    source: str


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


def check_scope_fields(scope: ScopeKind, project: str | None, env: str | None) -> None:
    """ValueError unless `project` and `env` are given exactly when `scope` needs them."""
    needs_project = scope != ScopeKind.ORG
    needs_env = scope == ScopeKind.ENVIRONMENT
    if (project is not None) != needs_project:
        raise ValueError(
            f"'project' is {'required' if needs_project else 'not allowed'} at {scope} scope"
        )
    if (env is not None) != needs_env:
        raise ValueError(f"'env' is {'required' if needs_env else 'not allowed'} at {scope} scope")


class ScopeFields(ApiModel):
    """A scope: `org`, or `project` with `project`, or `environment` with `project` and `env`."""

    scope: ScopeKind
    project: Key | None = None
    env: Key | None = None

    @model_validator(mode="after")
    def _fields_match_scope(self) -> Self:
        check_scope_fields(self.scope, self.project, self.env)
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

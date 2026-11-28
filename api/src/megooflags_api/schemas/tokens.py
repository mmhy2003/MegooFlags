import uuid
from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import AfterValidator, AwareDatetime, Field, model_validator

from megooflags_api.auth.rbac import Permission
from megooflags_api.models.enums import ApiTokenKind
from megooflags_api.schemas.common import ApiModel, Key, Name
from megooflags_api.schemas.identity import BindingOut, ScopeFields


def _in_the_future(value: datetime) -> datetime:
    if value <= datetime.now(UTC):
        raise ValueError("must be in the future")
    return value


Expiry = Annotated[AwareDatetime, AfterValidator(_in_the_future)]


class TokenBindingIn(ScopeFields):
    role: Key


class ServiceTokenCreate(ApiModel):
    name: Name
    expires_at: Expiry | None = None
    bindings: list[TokenBindingIn] = []


class TokenOut(ApiModel):
    id: uuid.UUID
    kind: ApiTokenKind
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    rotated_from_id: uuid.UUID | None
    bindings: list[BindingOut]


class TokenCreated(ApiModel):
    token: TokenOut
    secret: str = Field(description="The API token. Shown only once.")


class PersonalTokenCreate(ApiModel):
    """A token acting as you, limited to `permissions` within `project` (and `env`), and never
    more than you hold at the time of each request."""

    name: Name
    permissions: Annotated[list[Permission], Field(min_length=1)]
    project: Key | None = None
    env: Key | None = None
    expires_at: Expiry | None = None

    @model_validator(mode="after")
    def _env_needs_project(self) -> Self:
        if self.env is not None and self.project is None:
            raise ValueError("'env' needs 'project'")
        return self


class PersonalTokenOut(ApiModel):
    id: uuid.UUID
    name: str
    prefix: str
    permissions: list[Permission]
    project: str | None
    env: str | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


class PersonalTokenCreated(ApiModel):
    token: PersonalTokenOut
    secret: str = Field(description="The API token. Shown only once.")

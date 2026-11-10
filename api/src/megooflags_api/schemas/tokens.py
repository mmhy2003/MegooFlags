import uuid
from datetime import UTC, datetime

from pydantic import AwareDatetime, Field, field_validator

from megooflags_api.models.enums import ApiTokenKind
from megooflags_api.schemas.common import ApiModel, Key, Name
from megooflags_api.schemas.identity import BindingOut, ScopeFields


class TokenBindingIn(ScopeFields):
    role: Key


class ServiceTokenCreate(ApiModel):
    name: Name
    expires_at: AwareDatetime | None = None
    bindings: list[TokenBindingIn] = []

    @field_validator("expires_at")
    @classmethod
    def _in_the_future(cls, value: datetime | None) -> datetime | None:
        if value is not None and value <= datetime.now(UTC):
            raise ValueError("must be in the future")
        return value


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

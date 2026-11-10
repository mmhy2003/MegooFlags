import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import StringConstraints

from megooflags_api.auth.principal import PrincipalKind
from megooflags_api.models.enums import SessionState
from megooflags_api.schemas.common import ApiModel, Email, Name

# Long enough for any policy-compliant password; the cap keeps hashing cheap for junk input.
Password = Annotated[str, StringConstraints(max_length=1024)]


class SetupStatus(ApiModel):
    needs_setup: bool


class SetupIn(ApiModel):
    email: Email
    name: Name
    password: Password


class SignInIn(ApiModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=320)]
    password: Password


class SignInOut(ApiModel):
    status: Literal["signed_in", "mfa_required", "enrol_mfa"]
    mfa_methods: list[Literal["totp", "webauthn", "recovery"]] = []


class MfaStatus(ApiModel):
    totp: bool
    passkeys: int
    recovery_codes_left: int


class MeOut(ApiModel):
    kind: PrincipalKind
    id: uuid.UUID
    label: str
    email: str | None
    name: str | None
    auth_method: str
    session_state: SessionState | None
    mfa: MfaStatus | None
    require_mfa: bool


class PasswordChange(ApiModel):
    current_password: Password
    new_password: Password


class SessionOut(ApiModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    ip: str | None
    user_agent: str | None
    auth_method: str
    current: bool

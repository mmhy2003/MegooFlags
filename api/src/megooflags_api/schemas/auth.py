import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

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


class TotpEnrolment(ApiModel):
    """The new secret, shown once: type it in, or scan `otpauth_url` as a QR code."""

    secret: str
    otpauth_url: str


class CodeIn(ApiModel):
    """A TOTP code, or a recovery code (`xxxxx-xxxxx`, any case)."""

    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class RecoveryCodes(ApiModel):
    """Single-use codes, shown once; each replaces the second factor for one sign-in."""

    recovery_codes: list[str]


class TotpConfirmed(ApiModel):
    """`recovery_codes` is set when the account had none left, shown once."""

    recovery_codes: list[str] | None


class OrgSettingsOut(ApiModel):
    require_mfa: bool


class OrgSettingsIn(ApiModel):
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


class PasskeyOptionsOut(ApiModel):
    """Pass `options` to `navigator.credentials.create()` or `.get()` (after decoding the
    base64url fields), then send the result back with `challenge_id` within 5 minutes."""

    challenge_id: str
    options: dict[str, Any]


class PasskeyRegister(ApiModel):
    challenge_id: Annotated[str, StringConstraints(max_length=64)]
    name: Name
    credential: dict[str, Any]


class PasskeyOut(ApiModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None
    transports: list[str]


class PasskeyRename(ApiModel):
    name: Name


class PasskeyRegistered(ApiModel):
    """`recovery_codes` is set when the account had none left, shown once."""

    passkey: PasskeyOut
    recovery_codes: list[str] | None


class WebauthnVerifyIn(ApiModel):
    challenge_id: Annotated[str, StringConstraints(max_length=64)]
    credential: dict[str, Any]

"""Passkeys (WebAuthn, through py_webauthn): registration and assertion options and their
verification.

The relying party is `MEGOOFLAGS_PUBLIC_URL`: its hostname is the RP ID and its origin the only
accepted one. Without it, passkeys are unavailable (409 public_url_required). Each options call
stores its challenge in Valkey for 5 minutes, and verification takes it with GETDEL, so a challenge
works once. When Valkey cannot be reached, passkeys are unavailable (503).
"""

import binascii
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.registration.verify_registration_response import VerifiedRegistration

from megooflags_api.errors import public_url_required, unavailable
from megooflags_api.models import User, WebauthnCredential
from megooflags_api.settings import Settings

logger = logging.getLogger(__name__)

RP_NAME = "MegooFlags"
CHALLENGE_PREFIX = "megooflags:auth:webauthn:"
CHALLENGE_SECONDS = 300
_TRANSPORTS = {t.value for t in AuthenticatorTransport}
_VALKEY_ERRORS = (ValkeyError, OSError, TimeoutError)

Purpose = Literal["register", "authenticate"]


@dataclass(frozen=True)
class RelyingParty:
    id: str
    origin: str


def rp(settings: Settings) -> RelyingParty:
    """The relying party for `public_url`; 409 public_url_required without one."""
    if settings.public_url is None or settings.public_url.host is None:
        raise public_url_required()
    url = urlsplit(str(settings.public_url))
    return RelyingParty(id=settings.public_url.host, origin=f"{url.scheme}://{url.netloc}")


@dataclass(frozen=True)
class Challenge:
    challenge: bytes
    purpose: Purpose
    # Registration: the registering user. Second factor: the pending session and its user.
    # Passwordless sign-in: neither.
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None


async def save_challenge(client: Valkey, challenge: Challenge) -> str:
    """Store the challenge for 5 minutes; returns its id. 503 when Valkey cannot be reached."""
    challenge_id = secrets.token_urlsafe(24)
    value = json.dumps(
        {
            "challenge": bytes_to_base64url(challenge.challenge),
            "purpose": challenge.purpose,
            "user_id": None if challenge.user_id is None else str(challenge.user_id),
            "session_id": None if challenge.session_id is None else str(challenge.session_id),
        }
    )
    try:
        await client.set(CHALLENGE_PREFIX + challenge_id, value, ex=CHALLENGE_SECONDS)
    except _VALKEY_ERRORS:
        logger.warning("cannot store a passkey challenge", exc_info=True)
        raise unavailable("passkeys are unavailable right now; try again later") from None
    return challenge_id


async def take_challenge(client: Valkey, challenge_id: str) -> Challenge | None:
    """The stored challenge, removed so it works once; None when unknown, used or expired."""
    try:
        raw = await client.getdel(CHALLENGE_PREFIX + challenge_id)
    except _VALKEY_ERRORS:
        logger.warning("cannot read a passkey challenge", exc_info=True)
        raise unavailable("passkeys are unavailable right now; try again later") from None
    if raw is None:
        return None
    data = json.loads(raw)
    return Challenge(
        challenge=base64url_to_bytes(data["challenge"]),
        purpose=data["purpose"],
        user_id=None if data["user_id"] is None else uuid.UUID(data["user_id"]),
        session_id=None if data["session_id"] is None else uuid.UUID(data["session_id"]),
    )


def _descriptors(credentials: list[WebauthnCredential]) -> list[PublicKeyCredentialDescriptor]:
    return [
        PublicKeyCredentialDescriptor(
            id=c.credential_id,
            transports=[AuthenticatorTransport(t) for t in c.transports if t in _TRANSPORTS],
        )
        for c in credentials
    ]


def registration_options(
    party: RelyingParty, user: User, existing: list[WebauthnCredential]
) -> tuple[bytes, dict[str, Any]]:
    """The challenge and the `navigator.credentials.create()` options, as JSON."""
    options = generate_registration_options(
        rp_id=party.id,
        rp_name=RP_NAME,
        user_id=user.id.bytes,
        user_name=user.email,
        user_display_name=user.name,
        exclude_credentials=_descriptors(existing),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    return options.challenge, json.loads(options_to_json(options))


def verify_registration(
    party: RelyingParty, challenge: bytes, credential: dict[str, Any]
) -> VerifiedRegistration | None:
    """The verified new credential, or None when the response does not check out."""
    try:
        return verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=party.id,
            expected_origin=party.origin,
            require_user_verification=False,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError):
        return None


def transports(credential: dict[str, Any]) -> list[str]:
    """The known transports the browser reported for a new credential."""
    response = credential.get("response")
    reported = response.get("transports") if isinstance(response, dict) else None
    if not isinstance(reported, list):
        return []
    return sorted({t for t in reported if isinstance(t, str) and t in _TRANSPORTS})


def authentication_options(
    party: RelyingParty, allowed: list[WebauthnCredential], *, passwordless: bool
) -> tuple[bytes, dict[str, Any]]:
    """The challenge and the `navigator.credentials.get()` options, as JSON.

    A passwordless sign-in lists no credentials (the authenticator offers its discoverable ones)
    and requires user verification, since the passkey is the only factor.
    """
    options = generate_authentication_options(
        rp_id=party.id,
        allow_credentials=_descriptors(allowed),
        user_verification=(
            UserVerificationRequirement.REQUIRED
            if passwordless
            else UserVerificationRequirement.PREFERRED
        ),
    )
    return options.challenge, json.loads(options_to_json(options))


def raw_id(credential: dict[str, Any]) -> bytes | None:
    """The credential id of an assertion, or None when it is missing or malformed."""
    value = credential.get("rawId")
    if not isinstance(value, str):
        return None
    try:
        return base64url_to_bytes(value)
    except (binascii.Error, ValueError):
        return None


def verify_authentication(
    party: RelyingParty,
    challenge: bytes,
    credential: dict[str, Any],
    stored: WebauthnCredential,
    *,
    passwordless: bool,
) -> int | None:
    """The new sign count, or None when the assertion does not check out (including a counter
    that did not increase, a sign of a cloned authenticator)."""
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=party.id,
            expected_origin=party.origin,
            credential_public_key=stored.public_key,
            credential_current_sign_count=stored.sign_count,
            require_user_verification=passwordless,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError):
        return None
    return verified.new_sign_count

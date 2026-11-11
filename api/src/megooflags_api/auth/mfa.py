"""Second factors: TOTP (30-second steps, one step of clock drift either way) and single-use
recovery codes.

TOTP secrets are stored encrypted with the SecretBox; recovery codes as the sha256 of the code
without its dash, lowercased.
"""

import hashlib
import hmac
import secrets
import string
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import pyotp
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.crypto import SecretBox
from megooflags_api.models import RecoveryCode, TotpCredential, UserSession, WebauthnCredential
from megooflags_api.schemas.auth import MfaStatus

ISSUER = "MegooFlags"
STEP_SECONDS = 30
RECOVERY_CODE_COUNT = 10
# Removing the last factor or regenerating recovery codes needs a factor verified this recently.
REAUTH_WINDOW = timedelta(minutes=10)
_RECOVERY_ALPHABET = string.ascii_lowercase + string.digits

MfaMethod = Literal["totp", "webauthn", "recovery"]


def new_totp_secret() -> str:
    return pyotp.random_base32()


def otpauth_url(secret: str, email: str) -> str:
    """`otpauth://totp/MegooFlags:<email>?secret=...&issuer=MegooFlags`, for a QR code."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def verify_totp(cred: TotpCredential, code: str, box: SecretBox) -> int | None:
    """The step `code` is valid for (previous, current or next), or None.

    Steps at or before `cred.last_used_step` are refused, so a code works only once; the caller
    stores the returned step there.
    """
    totp = pyotp.TOTP(box.decrypt(cred.secret_encrypted))
    current = int(time.time()) // STEP_SECONDS
    for step in (current - 1, current, current + 1):
        if cred.last_used_step is not None and step <= cred.last_used_step:
            continue
        if hmac.compare_digest(totp.at(step * STEP_SECONDS).encode(), code.encode()):
            return step
    return None


def generate_recovery_codes() -> list[str]:
    """Ten random `xxxxx-xxxxx` codes (lowercase letters and digits)."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        chars = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{chars[:5]}-{chars[5:]}")
    return codes


def hash_recovery(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


async def user_factors(session: AsyncSession, user_id: uuid.UUID) -> MfaStatus:
    """Whether the user has confirmed TOTP, how many passkeys and unused recovery codes."""
    totp = exists().where(
        TotpCredential.user_id == user_id, TotpCredential.confirmed_at.is_not(None)
    )
    passkeys = (
        select(func.count())
        .select_from(WebauthnCredential)
        .where(WebauthnCredential.user_id == user_id)
        .scalar_subquery()
    )
    codes = (
        select(func.count())
        .select_from(RecoveryCode)
        .where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
        .scalar_subquery()
    )
    has_totp, passkey_count, codes_left = (
        await session.execute(select(totp, passkeys, codes))
    ).one()
    return MfaStatus(totp=has_totp, passkeys=passkey_count, recovery_codes_left=codes_left)


def has_factor(status: MfaStatus) -> bool:
    """TOTP or a passkey; recovery codes only stand in for one."""
    return status.totp or status.passkeys > 0


def sign_in_methods(status: MfaStatus) -> list[MfaMethod]:
    methods: list[MfaMethod] = []
    if status.totp:
        methods.append("totp")
    if status.passkeys:
        methods.append("webauthn")
    if status.recovery_codes_left:
        methods.append("recovery")
    return methods


def recently_verified(row: UserSession) -> bool:
    """Whether a factor was verified in this session within the last 10 minutes."""
    verified = row.mfa_verified_at
    return verified is not None and datetime.now(UTC) - verified <= REAUTH_WINDOW


async def replace_recovery_codes(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """Delete the user's recovery codes and add ten new ones; returns them. The caller commits."""
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    codes = generate_recovery_codes()
    session.add_all(RecoveryCode(user_id=user_id, code_hash=hash_recovery(c)) for c in codes)
    await session.flush()
    return codes


async def use_recovery_code(session: AsyncSession, user_id: uuid.UUID, code: str) -> bool:
    """Mark the matching unused code as used; False when there is none. The caller commits."""
    used = await session.scalar(
        update(RecoveryCode)
        .where(
            RecoveryCode.user_id == user_id,
            RecoveryCode.code_hash == hash_recovery(code),
            RecoveryCode.used_at.is_(None),
        )
        .values(used_at=func.now())
        .returning(RecoveryCode.id)
    )
    return used is not None


async def remove_totp(
    session: AsyncSession, user_id: uuid.UUID, *, with_recovery_codes: bool
) -> None:
    """Delete the user's TOTP credential, and their recovery codes if asked. The caller commits."""
    await session.execute(delete(TotpCredential).where(TotpCredential.user_id == user_id))
    if with_recovery_codes:
        await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))


async def clear_factors(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Delete every factor of the user: TOTP, recovery codes and passkeys. The caller commits."""
    await remove_totp(session, user_id, with_recovery_codes=True)
    await session.execute(delete(WebauthnCredential).where(WebauthnCredential.user_id == user_id))

"""API tokens (`mf_api_...`). Only the SHA-256 of a token is stored; the secret is shown once."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import ApiToken, User
from megooflags_api.models.enums import ApiTokenKind
from megooflags_api.services.sdk_keys import BASE62, hash_key

PREFIX = "mf_api_"
TOKEN_LENGTH = 39  # 7-char prefix + 32 random
DISPLAY_PREFIX = 14
TOUCH_INTERVAL = timedelta(minutes=5)


def generate_token() -> str:
    return PREFIX + "".join(secrets.choice(BASE62) for _ in range(TOKEN_LENGTH - len(PREFIX)))


def looks_like_token(value: str) -> bool:
    return (
        len(value) == TOKEN_LENGTH
        and value.startswith(PREFIX)
        and all(c in BASE62 for c in value[len(PREFIX) :])
    )


async def _create(session: AsyncSession, row: ApiToken) -> tuple[ApiToken, str]:
    token = generate_token()
    row.prefix = token[:DISPLAY_PREFIX]
    row.secret_hash = hash_key(token)
    session.add(row)
    await session.flush()
    return row, token


async def create_service_token(
    session: AsyncSession,
    name: str,
    *,
    created_by: uuid.UUID | None,
    expires_at: datetime | None,
) -> tuple[ApiToken, str]:
    return await _create(
        session,
        ApiToken(
            kind=ApiTokenKind.SERVICE, name=name, created_by=created_by, expires_at=expires_at
        ),
    )


async def create_personal_token(
    session: AsyncSession,
    owner: User,
    name: str,
    permissions: list[str],
    project_id: uuid.UUID | None,
    environment_id: uuid.UUID | None,
    expires_at: datetime | None,
) -> tuple[ApiToken, str]:
    return await _create(
        session,
        ApiToken(
            kind=ApiTokenKind.PERSONAL,
            name=name,
            owner_user_id=owner.id,
            created_by=owner.id,
            permissions=permissions,
            project_id=project_id,
            environment_id=environment_id,
            expires_at=expires_at,
        ),
    )


async def resolve_token(session: AsyncSession, token: str) -> ApiToken | None:
    """The live token for `token`, or None when it is unknown, revoked or expired."""
    if not looks_like_token(token):
        return None
    row = await session.scalar(select(ApiToken).where(ApiToken.secret_hash == hash_key(token)))
    if row is None or row.revoked_at is not None:
        return None
    if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
        return None
    return row


async def touch_last_used(session: AsyncSession, row: ApiToken) -> None:
    """Record use at most every 5 minutes, and commit, because read-only requests never commit."""
    now = datetime.now(UTC)
    if row.last_used_at is not None and now - row.last_used_at < TOUCH_INTERVAL:
        return
    row.last_used_at = now
    await session.commit()

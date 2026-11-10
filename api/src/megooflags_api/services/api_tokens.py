"""API tokens (`mf_api_...`). Only the SHA-256 of a token is stored; the secret is shown once."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import key_inactive, not_found, validation_error
from megooflags_api.models import ApiToken, RoleBinding, User
from megooflags_api.models.enums import ApiTokenKind, SubjectKind
from megooflags_api.schemas.tokens import TokenOut
from megooflags_api.services.bindings import bindings_of
from megooflags_api.services.pagination import decode_cursor, page_slice
from megooflags_api.services.sdk_keys import BASE62, hash_key, is_inactive

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


async def tokens_out(session: AsyncSession, rows: list[ApiToken]) -> list[TokenOut]:
    """The tokens with their role bindings, loaded in one query."""
    held = await bindings_of(session, SubjectKind.SERVICE_TOKEN, [row.id for row in rows])
    return [
        TokenOut(
            id=row.id,
            kind=row.kind,
            name=row.name,
            prefix=row.prefix,
            created_at=row.created_at,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            last_used_at=row.last_used_at,
            rotated_from_id=row.rotated_from_id,
            bindings=held[row.id],
        )
        for row in rows
    ]


async def token_out(session: AsyncSession, row: ApiToken) -> TokenOut:
    return (await tokens_out(session, [row]))[0]


async def list_service_tokens(
    session: AsyncSession, cursor: str | None, limit: int
) -> tuple[list[TokenOut], str | None]:
    """Service tokens by id, revoked and expired ones included."""
    query = (
        select(ApiToken)
        .where(ApiToken.kind == ApiTokenKind.SERVICE)
        .order_by(ApiToken.id)
        .limit(limit + 1)
    )
    if cursor is not None:
        try:
            after = uuid.UUID(decode_cursor(cursor))
        except ValueError:
            raise validation_error("invalid cursor", "cursor") from None
        query = query.where(ApiToken.id > after)
    rows = list(await session.scalars(query))
    page, next_cursor = page_slice(rows, limit, lambda row: str(row.id))
    return await tokens_out(session, page), next_cursor


async def get_service_token(
    session: AsyncSession, token_id: uuid.UUID, *, for_update: bool = False
) -> ApiToken:
    """404 for an unknown id and for personal tokens."""
    row = await session.get(
        ApiToken, token_id, with_for_update=for_update, populate_existing=for_update
    )
    if row is None or row.kind != ApiTokenKind.SERVICE:
        raise not_found("token", str(token_id))
    return row


async def revoke(session: AsyncSession, row: ApiToken) -> None:
    """Revoke at once; revoking again keeps the first `revoked_at`."""
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.flush()


async def rotate_token(
    session: AsyncSession, token_id: uuid.UUID, overlap: timedelta, *, created_by: uuid.UUID | None
) -> tuple[ApiToken, ApiToken, str]:
    """Replace a live service token with a new one of the same name, expiry and role bindings.
    The old token keeps working for `overlap` (never longer than it already would); a zero
    overlap revokes it at once.

    Returns the old row, the new row and the new secret.
    """
    old = await get_service_token(session, token_id, for_update=True)
    now = datetime.now(UTC)
    if is_inactive(old.revoked_at, old.expires_at, now):
        raise key_inactive("token", str(token_id))
    new, secret = await _create(
        session,
        ApiToken(
            kind=ApiTokenKind.SERVICE,
            name=old.name,
            created_by=created_by,
            expires_at=old.expires_at,
            rotated_from_id=old.id,
        ),
    )
    held = await session.scalars(
        select(RoleBinding).where(
            RoleBinding.subject_kind == SubjectKind.SERVICE_TOKEN, RoleBinding.subject_id == old.id
        )
    )
    for binding in held.all():
        session.add(
            RoleBinding(
                subject_kind=SubjectKind.SERVICE_TOKEN,
                subject_id=new.id,
                role_id=binding.role_id,
                scope=binding.scope,
                project_id=binding.project_id,
                environment_id=binding.environment_id,
                source=binding.source,
            )
        )
    if overlap <= timedelta(0):
        old.revoked_at = now
    else:
        until = now + overlap
        old.expires_at = until if old.expires_at is None else min(old.expires_at, until)
    await session.flush()
    return old, new, secret


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

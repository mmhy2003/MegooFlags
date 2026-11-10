"""Per-environment SDK keys. Only the SHA-256 of a key is stored; the secret is shown once."""

import hashlib
import secrets
import string
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import not_found
from megooflags_api.models import Environment, SdkKey
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.schemas.sdk_keys import SdkKeyCreate

KEY_PREFIXES = {SdkKeyKind.SERVER: "mf_server_", SdkKeyKind.CLIENT: "mf_client_"}
BASE62 = string.digits + string.ascii_letters
KEY_LENGTH = 42  # 10-char prefix + 32 random
DISPLAY_PREFIX = 14


def generate_key(kind: SdkKeyKind) -> str:
    prefix = KEY_PREFIXES[kind]
    return prefix + "".join(secrets.choice(BASE62) for _ in range(KEY_LENGTH - len(prefix)))


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_kind(key: str) -> SdkKeyKind | None:
    if len(key) != KEY_LENGTH:
        return None
    for kind, prefix in KEY_PREFIXES.items():
        if key.startswith(prefix):
            return kind
    return None


async def create_key(
    session: AsyncSession, environment: Environment, body: SdkKeyCreate
) -> tuple[SdkKey, str]:
    key = generate_key(body.kind)
    row = SdkKey(
        environment_id=environment.id,
        kind=body.kind,
        name=body.name,
        prefix=key[:DISPLAY_PREFIX],
        secret_hash=hash_key(key),
    )
    session.add(row)
    await session.flush()
    return row, key


async def list_keys(session: AsyncSession, environment: Environment) -> Sequence[SdkKey]:
    rows = await session.scalars(
        select(SdkKey)
        .where(SdkKey.environment_id == environment.id)
        .order_by(SdkKey.created_at, SdkKey.id)
    )
    return rows.all()


async def revoke_key(session: AsyncSession, environment: Environment, key_id: uuid.UUID) -> SdkKey:
    """Revoking twice keeps the first revoked_at."""
    row = await session.scalar(
        select(SdkKey).where(SdkKey.id == key_id, SdkKey.environment_id == environment.id)
    )
    if row is None:
        raise not_found("sdk key", str(key_id))
    if row.revoked_at is None:
        row.revoked_at = await session.scalar(select(func.now()))
        await session.flush()
    return row

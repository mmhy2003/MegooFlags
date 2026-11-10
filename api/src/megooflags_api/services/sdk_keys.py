"""Per-environment SDK keys. Only the SHA-256 of a key is stored; the secret is shown once."""

import hashlib
import secrets
import string
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import key_inactive, not_found
from megooflags_api.models import Environment, SdkKey
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.schemas.sdk_keys import SdkKeyCreate
from megooflags_api.services.ruleset import EnvRef

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
    session: AsyncSession,
    environment: Environment,
    body: SdkKeyCreate,
    rotated_from_id: uuid.UUID | None = None,
) -> tuple[SdkKey, str]:
    key = generate_key(body.kind)
    row = SdkKey(
        environment_id=environment.id,
        kind=body.kind,
        name=body.name,
        prefix=key[:DISPLAY_PREFIX],
        secret_hash=hash_key(key),
        rotated_from_id=rotated_from_id,
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


async def get_key(
    session: AsyncSession, environment: Environment, key_id: uuid.UUID, *, for_update: bool = False
) -> SdkKey:
    query = select(SdkKey).where(SdkKey.id == key_id, SdkKey.environment_id == environment.id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None:
        raise not_found("sdk key", str(key_id))
    return row


async def revoke_key(session: AsyncSession, row: SdkKey) -> None:
    """Revoking twice keeps the first revoked_at."""
    if row.revoked_at is None:
        row.revoked_at = await session.scalar(select(func.now()))
        await session.flush()


def is_inactive(revoked_at: datetime | None, expires_at: datetime | None, now: datetime) -> bool:
    """A revoked key and a key past its expiry are both refused."""
    return revoked_at is not None or (expires_at is not None and expires_at <= now)


async def rotate_key(
    session: AsyncSession, environment: Environment, key_id: uuid.UUID, overlap: timedelta
) -> tuple[SdkKey, SdkKey, str]:
    """Replace a live key with a new one of the same kind and name. The old key keeps working
    for `overlap` (never longer than it already would); a zero overlap revokes it at once.

    Returns the old row, the new row and the new secret.
    """
    old = await get_key(session, environment, key_id, for_update=True)
    now = await session.scalar(select(func.now()))
    assert now is not None
    if is_inactive(old.revoked_at, old.expires_at, now):
        raise key_inactive("sdk key", str(key_id))
    new, key = await create_key(
        session, environment, SdkKeyCreate(name=old.name, kind=old.kind), rotated_from_id=old.id
    )
    if overlap <= timedelta(0):
        old.revoked_at = now
    else:
        until = now + overlap
        old.expires_at = until if old.expires_at is None else min(old.expires_at, until)
    await session.flush()
    return old, new, key


@dataclass(frozen=True, slots=True)
class SdkPrincipal:
    key_id: uuid.UUID
    kind: SdkKeyKind
    secret_hash: str
    environment: EnvRef
    revoked: bool
    expires_at: datetime | None = None

    def inactive(self, now: datetime) -> bool:
        """Revoked, or expired at `now`. Cached principals stay correct about expiry."""
        return self.revoked or (self.expires_at is not None and self.expires_at <= now)


class KeyResolver:
    """Resolves SDK keys to principals, caching found keys (active or revoked) for `ttl` seconds.

    Unknown hashes are not cached, so a key works as soon as it is created. A revocation is seen
    after the TTL, or at once when `forget` is called for the key. The principal carries the
    key's `expires_at`, so callers see an expiry at once through `SdkPrincipal.inactive`.
    """

    def __init__(self, ttl: float = 10.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._entries: dict[str, tuple[SdkPrincipal, float]] = {}

    async def resolve(self, session: AsyncSession, key: str) -> SdkPrincipal | None:
        if key_kind(key) is None:
            return None
        return await self.resolve_hash(session, hash_key(key))

    async def resolve_hash(self, session: AsyncSession, secret_hash: str) -> SdkPrincipal | None:
        entry = self._entries.get(secret_hash)
        if entry is not None and self._clock() - entry[1] < self._ttl:
            return entry[0]
        started = self._clock()
        row = (
            await session.execute(
                select(
                    SdkKey.id,
                    SdkKey.kind,
                    SdkKey.revoked_at,
                    SdkKey.expires_at,
                    Environment.id.label("environment_id"),
                    Environment.project_id,
                    Environment.key,
                )
                .join(Environment, Environment.id == SdkKey.environment_id)
                .where(SdkKey.secret_hash == secret_hash)
            )
        ).one_or_none()
        if row is None:
            self._entries.pop(secret_hash, None)
            return None
        principal = SdkPrincipal(
            key_id=row.id,
            kind=row.kind,
            secret_hash=secret_hash,
            environment=EnvRef(id=row.environment_id, project_id=row.project_id, key=row.key),
            revoked=row.revoked_at is not None,
            expires_at=row.expires_at,
        )
        self._entries[secret_hash] = (principal, started)
        return principal

    def forget(self, key_id: uuid.UUID) -> None:
        for secret_hash in [h for h, (p, _) in self._entries.items() if p.key_id == key_id]:
            del self._entries[secret_hash]

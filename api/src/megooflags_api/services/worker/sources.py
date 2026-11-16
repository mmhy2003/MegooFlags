"""SDK delivery on a worker: rulesets and keys from the snapshot store, a per-process rate limit.

A worker has no Postgres and no Valkey, so these implement the `services/delivery.py` interfaces
on the `SnapshotStore`. Reading a ruleset may parse a snapshot file, so it runs in a thread.
"""

import asyncio
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.ruleset_cache import CachedRuleset
from megooflags_api.services.sdk_keys import SdkPrincipal, hash_key, key_kind
from megooflags_api.services.worker.store import SnapshotStore


class StoreRulesetSource:
    def __init__(self, store: SnapshotStore) -> None:
        self._store = store

    async def get_entry(self, environment: EnvRef) -> CachedRuleset:
        entry = await asyncio.to_thread(self._store.entry, environment.id)
        if entry is None:
            # Keys come from the store's index, so a route never asks for an unknown environment.
            raise RuntimeError(f"no snapshot for environment {environment.id}")
        return entry


class StoreKeySource:
    def __init__(self, store: SnapshotStore) -> None:
        self._store = store

    async def resolve(self, key: str) -> SdkPrincipal | None:
        if key_kind(key) is None:
            return None
        return self._store.principal(hash_key(key))

    async def resolve_hash(self, secret_hash: str) -> SdkPrincipal | None:
        return self._store.principal(secret_hash)


class StoreStreamSource:
    def __init__(self, store: SnapshotStore) -> None:
        self._store = store

    async def current(self, environment: EnvRef) -> CachedRuleset | None:
        return await asyncio.to_thread(self._store.entry, environment.id)

    async def inactive_keys(self, key_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """Revoked or expired keys, and keys no snapshot carries any more (deleted)."""
        now = datetime.now(UTC)
        found = self._store.keys_by_id(key_ids)
        return {
            key_id
            for key_id in key_ids
            if (key := found.get(key_id)) is None
            or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at <= now)
        }


class MemoryRateLimiter:
    """The client-key rate limit of `RateLimiter`, counted in this process only."""

    def __init__(self, limit_per_minute: int, clock: Callable[[], float] = time.time) -> None:
        self._limit = limit_per_minute
        self._clock = clock
        self._window = -1
        self._counts: dict[tuple[uuid.UUID, str], int] = {}

    def __len__(self) -> int:
        """The number of key and client pairs counted in the current window."""
        return len(self._counts)

    async def hit(self, key_id: uuid.UUID, client_ip: str) -> int | None:
        """Count one request. Returns the Retry-After seconds when over the limit, else None."""
        now = self._clock()
        window = int(now // 60)
        if window != self._window:
            self._window = window
            self._counts.clear()
        count = self._counts.get((key_id, client_ip), 0) + 1
        self._counts[(key_id, client_ip)] = count
        if count > self._limit:
            return max(1, 60 - int(now % 60))
        return None

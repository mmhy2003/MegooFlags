"""Parsed rulesets per environment, kept in memory with their JSON bytes and ETag.

A lookup with `max_age=0` runs one fingerprint query over the versions and timestamps that a write
changes, so a write through any API replica is seen by the next lookup on every replica. SDK reads
may trust an entry for `max_age` seconds after its last fingerprint check. After that, a cache
with a sessionmaker keeps serving the entry while one background task checks the fingerprint and
rebuilds, so no SDK request waits for the check. A Valkey change message invalidates the entries
of its project at once: the next lookup waits for a fresh check instead of serving the old entry.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.evaluation import Ruleset, parse_ruleset
from megooflags_api.services.ruleset import EnvRef, build_ruleset

logger = logging.getLogger(__name__)

SDK_MAX_AGE = 1.0

FINGERPRINT = text("""
SELECT md5(
  coalesce((SELECT string_agg(fe.id::text || ':' || fe.version::text, ',' ORDER BY fe.id)
            FROM flag_environment fe WHERE fe.environment_id = :environment_id), '')
  || '|' ||
  coalesce((SELECT string_agg(f.id::text || ':' || f.updated_at::text, ',' ORDER BY f.id)
            FROM flag f WHERE f.project_id = :project_id AND f.archived_at IS NULL), '')
  || '|' ||
  coalesce((SELECT string_agg(s.id::text || ':' || s.updated_at::text, ',' ORDER BY s.id)
            FROM segment s WHERE s.project_id = :project_id), ''))
""")


@dataclass(frozen=True, slots=True)
class CachedRuleset:
    """A parsed ruleset with the JSON body SDKs download and its strong ETag."""

    fingerprint: str
    ruleset: Ruleset
    body: bytes
    etag: str


@dataclass(slots=True)
class _Entry:
    cached: CachedRuleset
    project_id: uuid.UUID
    checked_at: float
    checked_epoch: int  # the cache's epoch when the last check started
    invalidated_epoch: int = -1  # newer than checked_epoch: a change message came since


def _prepare(raw: dict[str, Any], fingerprint: str) -> CachedRuleset:
    body = json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode()
    return CachedRuleset(
        fingerprint=fingerprint,
        ruleset=parse_ruleset(raw),
        body=body,
        etag='"' + hashlib.sha256(body).hexdigest()[:32] + '"',
    )


class RulesetCache:
    """Parsed rulesets per environment, rebuilt only when the fingerprint changes."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        max_environments: int = 256,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._max = max_environments
        self._clock = clock
        self._entries: OrderedDict[uuid.UUID, _Entry] = OrderedDict()
        # Only environments with a lookup in progress hold a lock: (lock, [number of users]).
        self._locks: dict[uuid.UUID, tuple[asyncio.Lock, list[int]]] = {}
        self._refreshes: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._epoch = 0

    async def get(
        self, session: AsyncSession, environment: EnvRef, max_age: float = 0.0
    ) -> Ruleset:
        return (await self.get_entry(session, environment, max_age)).ruleset

    async def get_entry(
        self, session: AsyncSession, environment: EnvRef, max_age: float = 0.0
    ) -> CachedRuleset:
        """The ruleset of `environment`, trusted without a query for `max_age` seconds."""
        hit = self._fresh(environment.id, max_age)
        if hit is not None:
            return hit
        if max_age > 0:
            stale = self._stale(environment.id)
            if stale is not None and self._sessionmaker is not None:
                self._refresh_in_background(environment, max_age)
                return stale
            # Single flight: one fingerprint check per environment at a time, and the lookups
            # that waited for it reuse its result instead of each running the query.
            async with self._lock(environment.id):
                return await self._check(session, environment, max_age)
        # max_age=0 always checks, so a write is seen by the next read; checks run in parallel.
        epoch = self._epoch
        started, fingerprint = await self._fingerprint(session, environment)
        hit = self._lookup(environment.id, fingerprint, started, epoch)
        if hit is not None:
            return hit
        async with self._lock(environment.id):
            hit = self._lookup(environment.id, fingerprint, started, epoch)  # rebuilt meanwhile
            if hit is not None:
                return hit
            return await self._rebuild(session, environment, fingerprint, started, epoch)

    def invalidate(self, project_id: uuid.UUID) -> None:
        """Make the next lookup of every environment of `project_id` wait for a fresh check."""
        self._epoch += 1
        for entry in self._entries.values():
            if entry.project_id == project_id:
                entry.invalidated_epoch = self._epoch

    async def aclose(self) -> None:
        """Cancel the background checks still running."""
        tasks = list(self._refreshes.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _fresh(self, environment_id: uuid.UUID, max_age: float) -> CachedRuleset | None:
        entry = self._entries.get(environment_id)
        if (
            entry is None
            or max_age <= 0
            or entry.invalidated_epoch > entry.checked_epoch
            or self._clock() - entry.checked_at >= max_age
        ):
            return None
        self._entries.move_to_end(environment_id)
        return entry.cached

    def _stale(self, environment_id: uuid.UUID) -> CachedRuleset | None:
        """An entry that may be served while it is checked: older than max_age, not invalidated."""
        entry = self._entries.get(environment_id)
        if entry is None or entry.invalidated_epoch > entry.checked_epoch:
            return None
        self._entries.move_to_end(environment_id)
        return entry.cached

    def _refresh_in_background(self, environment: EnvRef, max_age: float) -> None:
        if environment.id in self._refreshes:
            return
        task = asyncio.create_task(self._refresh(environment, max_age))
        self._refreshes[environment.id] = task
        task.add_done_callback(lambda _: self._refreshes.pop(environment.id, None))

    async def _refresh(self, environment: EnvRef, max_age: float) -> None:
        assert self._sessionmaker is not None
        try:
            async with self._lock(environment.id), self._sessionmaker() as session:
                await self._check(session, environment, max_age)
        except Exception:
            # The entry stays stale, so the next lookup starts another check.
            logger.exception("ruleset check failed", extra={"environment_id": environment.id})

    async def _check(
        self, session: AsyncSession, environment: EnvRef, max_age: float
    ) -> CachedRuleset:
        """Check the fingerprint and rebuild if it changed; the caller holds the lock."""
        hit = self._fresh(environment.id, max_age)
        if hit is not None:
            return hit
        epoch = self._epoch
        started, fingerprint = await self._fingerprint(session, environment)
        hit = self._lookup(environment.id, fingerprint, started, epoch)
        if hit is not None:
            return hit
        return await self._rebuild(session, environment, fingerprint, started, epoch)

    @contextlib.asynccontextmanager
    async def _lock(self, environment_id: uuid.UUID) -> AsyncIterator[None]:
        lock, users = self._locks.setdefault(environment_id, (asyncio.Lock(), [0]))
        users[0] += 1
        try:
            async with lock:
                yield
        finally:
            users[0] -= 1
            if users[0] == 0:
                del self._locks[environment_id]

    async def _fingerprint(self, session: AsyncSession, environment: EnvRef) -> tuple[float, str]:
        # Read before the build: a commit in between only causes one extra rebuild later.
        started = self._clock()
        fingerprint: str = await session.scalar(
            FINGERPRINT,
            {"environment_id": environment.id, "project_id": environment.project_id},
        )
        return started, fingerprint

    async def _rebuild(
        self,
        session: AsyncSession,
        environment: EnvRef,
        fingerprint: str,
        started: float,
        epoch: int,
    ) -> CachedRuleset:
        """Build and store the ruleset; the caller holds the environment's lock."""
        raw = await build_ruleset(session, environment)
        # Parsing, encoding and hashing 10k flags takes hundreds of milliseconds: keep them off
        # the event loop so other requests on this replica are served meanwhile.
        cached = await asyncio.to_thread(_prepare, raw, fingerprint)
        entry = _Entry(cached, environment.project_id, started, epoch)
        old = self._entries.get(environment.id)
        if old is not None:
            entry.invalidated_epoch = old.invalidated_epoch  # a change message during the build
        self._entries[environment.id] = entry
        self._entries.move_to_end(environment.id)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)
        return cached

    def _lookup(
        self, environment_id: uuid.UUID, fingerprint: str, checked_at: float, epoch: int
    ) -> CachedRuleset | None:
        entry = self._entries.get(environment_id)
        if entry is None or entry.cached.fingerprint != fingerprint:
            return None
        entry.checked_at = checked_at
        entry.checked_epoch = epoch
        self._entries.move_to_end(environment_id)
        return entry.cached

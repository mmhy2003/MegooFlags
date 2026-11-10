"""Parsed rulesets per environment, kept in memory with their JSON bytes and ETag.

A lookup with `max_age=0` runs one fingerprint query over the versions and timestamps that a write
changes, so a write through any API replica is seen by the next lookup on every replica. SDK reads
may trust an entry for `max_age` seconds after its last fingerprint check; a Valkey change message
invalidates the entries of its project at once, so the next lookup checks the fingerprint again.
"""

import asyncio
import hashlib
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.evaluation import Ruleset, parse_ruleset
from megooflags_api.services.ruleset import EnvRef, build_ruleset

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


class RulesetCache:
    """Parsed rulesets per environment, rebuilt only when the fingerprint changes."""

    def __init__(
        self, max_environments: int = 256, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max = max_environments
        self._clock = clock
        self._entries: OrderedDict[uuid.UUID, _Entry] = OrderedDict()
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}

    async def get(
        self, session: AsyncSession, environment: EnvRef, max_age: float = 0.0
    ) -> Ruleset:
        return (await self.get_entry(session, environment, max_age)).ruleset

    async def get_entry(
        self, session: AsyncSession, environment: EnvRef, max_age: float = 0.0
    ) -> CachedRuleset:
        """The ruleset of `environment`, trusted without a query for `max_age` seconds."""
        entry = self._entries.get(environment.id)
        if entry is not None and max_age > 0 and self._clock() - entry.checked_at < max_age:
            self._entries.move_to_end(environment.id)
            return entry.cached
        # Read before the build: a commit in between only causes one extra rebuild later.
        started = self._clock()
        fingerprint: str = await session.scalar(
            FINGERPRINT,
            {"environment_id": environment.id, "project_id": environment.project_id},
        )
        hit = self._lookup(environment.id, fingerprint, started)
        if hit is not None:
            return hit
        lock = self._locks.setdefault(environment.id, asyncio.Lock())
        async with lock:
            hit = self._lookup(environment.id, fingerprint, started)  # another request rebuilt it
            if hit is not None:
                return hit
            raw = await build_ruleset(session, environment)
            body = json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode()
            cached = CachedRuleset(
                fingerprint=fingerprint,
                ruleset=parse_ruleset(raw),
                body=body,
                etag='"' + hashlib.sha256(body).hexdigest()[:32] + '"',
            )
            self._entries[environment.id] = _Entry(cached, environment.project_id, started)
            self._entries.move_to_end(environment.id)
            while len(self._entries) > self._max:
                evicted, _ = self._entries.popitem(last=False)
                self._locks.pop(evicted, None)
            return cached

    def invalidate(self, project_id: uuid.UUID) -> None:
        """Make the next lookup of every environment of `project_id` check its fingerprint."""
        for entry in self._entries.values():
            if entry.project_id == project_id:
                entry.checked_at = float("-inf")

    def _lookup(
        self, environment_id: uuid.UUID, fingerprint: str, checked_at: float
    ) -> CachedRuleset | None:
        entry = self._entries.get(environment_id)
        if entry is None or entry.cached.fingerprint != fingerprint:
            return None
        entry.checked_at = checked_at
        self._entries.move_to_end(environment_id)
        return entry.cached

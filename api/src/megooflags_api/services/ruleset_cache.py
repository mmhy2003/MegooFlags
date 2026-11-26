"""Parsed rulesets per environment, kept in memory with their JSON bytes and ETag.

A lookup with `max_age=0` runs one fingerprint query over the versions and timestamps that a write
changes, so a write through any API replica is seen by the next lookup on every replica. SDK reads
may trust an entry for `max_age` seconds after its last fingerprint check. After that, a cache
with a sessionmaker keeps serving the entry while one background task checks the fingerprint and
rebuilds, so no SDK request waits for the check. A Valkey change message invalidates the entries
of its project at once: the next lookup waits for a fresh check instead of serving the old entry.
When only flag_environment versions moved, a rebuild reloads just the changed flags and patches
their fragments into the old body, which gives the same bytes, ETag and ruleset as a full build.
"""

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.evaluation import Ruleset, parse_flag, parse_ruleset
from megooflags_api.services.ruleset import (
    SCHEMA_VERSION,
    EnvRef,
    FlagParts,
    RulesetParts,
    build_flags,
    build_parts,
    flag_versions,
)

logger = logging.getLogger(__name__)

SDK_MAX_AGE = 1.0
INCREMENTAL_MAX_FLAGS = 256  # beyond this many changed flags, a full build is cheaper

# Three parts, flag_environment | flag | segment, so a rebuild can tell what moved.
FINGERPRINT = text("""
SELECT
  md5(coalesce((SELECT string_agg(fe.id::text || ':' || fe.version::text, ',' ORDER BY fe.id)
                FROM flag_environment fe WHERE fe.environment_id = :environment_id), ''))
  || '|' ||
  md5(coalesce((SELECT string_agg(f.id::text || ':' || f.updated_at::text, ',' ORDER BY f.id)
                FROM flag f WHERE f.project_id = :project_id AND f.archived_at IS NULL), ''))
  || '|' ||
  md5(coalesce((SELECT string_agg(s.id::text || ':' || s.updated_at::text, ',' ORDER BY s.id)
                FROM segment s WHERE s.project_id = :project_id), ''))
""")


@dataclass(frozen=True, slots=True)
class CachedRuleset:
    """A parsed ruleset with the JSON body SDKs download and its strong ETag."""

    fingerprint: str
    ruleset: Ruleset
    body: bytes
    etag: str
    sorted_keys: tuple[str, ...]  # the ruleset's flag keys, sorted once for evaluate_all


@dataclass(frozen=True, slots=True)
class _Parts:
    """What an incremental rebuild reuses from the entry it replaces; never mutated."""

    head: bytes  # {"schema_version":1,"project":..,"environment":..,"flags":{
    order: tuple[str, ...]  # flag keys in body order
    fragments: dict[str, bytes]  # key -> b'"<key>":{...}'
    tail: bytes  # },"segments":{...}}
    ids: dict[uuid.UUID, str]
    versions: dict[uuid.UUID, int]


@dataclass(slots=True)
class _Entry:
    cached: CachedRuleset
    parts: _Parts
    project_id: uuid.UUID
    checked_at: float
    checked_epoch: int  # the cache's epoch when the last check started
    invalidated_epoch: int = -1  # newer than checked_epoch: a change message came since


def _etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def _encode(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def _body(head: bytes, order: Iterable[str], fragments: dict[str, bytes], tail: bytes) -> bytes:
    return head + b",".join(fragments[k] for k in order) + tail


def _prepare(parts: RulesetParts, fingerprint: str) -> tuple[CachedRuleset, _Parts]:
    """The entry of a full build; its body equals the encoded `parts.to_json()`."""
    head = (
        b'{"schema_version":'
        + _encode(SCHEMA_VERSION)
        + b',"project":'
        + _encode(parts.project)
        + b',"environment":'
        + _encode(parts.environment)
        + b',"flags":{'
    )
    fragments = {k: _encode(k) + b":" + _encode(v) for k, v in parts.flags.flags.items()}
    tail = b'},"segments":' + _encode(parts.segments) + b"}"
    order = tuple(parts.flags.flags)
    body = _body(head, order, fragments, tail)
    ruleset = parse_ruleset(parts.to_json())
    cached = CachedRuleset(
        fingerprint=fingerprint,
        ruleset=ruleset,
        body=body,
        etag=_etag(body),
        sorted_keys=tuple(sorted(ruleset.flags)),
    )
    return cached, _Parts(head, order, fragments, tail, parts.flags.ids, parts.flags.versions)


def _patch(
    cached: CachedRuleset, parts: _Parts, changed: FlagParts, fingerprint: str
) -> tuple[CachedRuleset, _Parts]:
    """A copy of the entry with the changed flags re-encoded and re-parsed; the old one is kept."""
    fragments = dict(parts.fragments)
    flags = dict(cached.ruleset.flags)  # same order: only existing keys are replaced
    for key, raw in changed.flags.items():
        fragments[key] = _encode(key) + b":" + _encode(raw)
        flags[key] = parse_flag(key, raw)
    body = _body(parts.head, parts.order, fragments, parts.tail)
    ruleset = dataclasses.replace(cached.ruleset, flags=flags)
    new_parts = dataclasses.replace(
        parts, fragments=fragments, versions={**parts.versions, **changed.versions}
    )
    return CachedRuleset(fingerprint, ruleset, body, _etag(body), cached.sorted_keys), new_parts


def _only_versions_moved(old: str, new: str) -> bool:
    """Only the flag_environment part of the fingerprint differs."""
    return old.split("|")[1:] == new.split("|")[1:]


def from_body(body: bytes) -> CachedRuleset:
    """A ruleset from the JSON bytes SDKs download, with the same ETag the cache gives them."""
    etag = _etag(body)
    ruleset = parse_ruleset(json.loads(body))
    return CachedRuleset(
        fingerprint=etag,
        ruleset=ruleset,
        body=body,
        etag=etag,
        sorted_keys=tuple(sorted(ruleset.flags)),
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
        if max_age > 0:
            hit = self.peek(environment, max_age)
            if hit is not None:
                return hit
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

    def peek(self, environment: EnvRef, max_age: float) -> CachedRuleset | None:
        """The entry for an SDK read without a session: fresh within max_age, or stale while one
        background check runs; None when the caller must wait for a check."""
        hit = self._fresh(environment.id, max_age)
        if hit is not None or self._sessionmaker is None:
            return hit
        stale = self._stale(environment.id)
        if stale is not None:
            self._refresh_in_background(environment, max_age)
        return stale

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
        old = self._entries.get(environment.id)
        built = None
        if old is not None and _only_versions_moved(old.cached.fingerprint, fingerprint):
            try:
                built = await self._incremental(session, environment, old, fingerprint)
            except Exception:
                logger.exception(
                    "incremental ruleset rebuild failed; rebuilding in full",
                    extra={"environment_id": environment.id},
                )
        if built is None:
            parts = await build_parts(session, environment)
            # Parsing, encoding and hashing 10k flags takes hundreds of milliseconds: keep them
            # off the event loop so other requests on this replica are served meanwhile.
            built = await asyncio.to_thread(_prepare, parts, fingerprint)
        cached, kept = built
        entry = _Entry(cached, kept, environment.project_id, started, epoch)
        old = self._entries.get(environment.id)
        if old is not None:
            entry.invalidated_epoch = old.invalidated_epoch  # a change message during the build
        self._entries[environment.id] = entry
        self._entries.move_to_end(environment.id)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)
        return cached

    async def _incremental(
        self, session: AsyncSession, environment: EnvRef, old: _Entry, fingerprint: str
    ) -> tuple[CachedRuleset, _Parts] | None:
        """The old entry with only the flags whose version moved reloaded; None for a full build."""
        versions = await flag_versions(session, environment)
        if versions.keys() != old.parts.versions.keys():
            return None
        changed = [i for i, v in versions.items() if old.parts.versions[i] != v]
        if len(changed) > INCREMENTAL_MAX_FLAGS:
            return None
        if not changed:  # e.g. an archived flag's row moved
            return dataclasses.replace(old.cached, fingerprint=fingerprint), old.parts
        flags = await build_flags(session, environment, changed)
        if set(flags.ids) != set(changed) or not set(flags.flags) <= old.parts.fragments.keys():
            return None  # archived or renamed meanwhile
        return await asyncio.to_thread(_patch, old.cached, old.parts, flags, fingerprint)

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

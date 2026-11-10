"""Parsed rulesets per environment, kept in memory and checked against the database on each use.

Every lookup runs one fingerprint query over the versions and timestamps that a write changes, so
a write through any API replica is seen by the next lookup on every replica.
"""

import asyncio
import uuid
from collections import OrderedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.evaluation import Ruleset, parse_ruleset
from megooflags_api.models import Environment
from megooflags_api.services.ruleset import build_ruleset

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


class RulesetCache:
    """Parsed rulesets per environment, rebuilt only when the fingerprint changes.

    Every lookup runs one fingerprint query, so the cache is correct across API replicas.
    """

    def __init__(self, max_environments: int = 256) -> None:
        self._max = max_environments
        self._entries: OrderedDict[uuid.UUID, tuple[str, Ruleset]] = OrderedDict()
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}

    async def get(self, session: AsyncSession, environment: Environment) -> Ruleset:
        # Read before the build: a commit in between only causes one extra rebuild later.
        fingerprint: str = await session.scalar(
            FINGERPRINT,
            {"environment_id": environment.id, "project_id": environment.project_id},
        )
        hit = self._lookup(environment.id, fingerprint)
        if hit is not None:
            return hit
        lock = self._locks.setdefault(environment.id, asyncio.Lock())
        async with lock:
            hit = self._lookup(environment.id, fingerprint)  # another request rebuilt it
            if hit is not None:
                return hit
            ruleset = parse_ruleset(await build_ruleset(session, environment))
            self._entries[environment.id] = (fingerprint, ruleset)
            self._entries.move_to_end(environment.id)
            while len(self._entries) > self._max:
                evicted, _ = self._entries.popitem(last=False)
                self._locks.pop(evicted, None)
            return ruleset

    def _lookup(self, environment_id: uuid.UUID, fingerprint: str) -> Ruleset | None:
        entry = self._entries.get(environment_id)
        if entry is None or entry[0] != fingerprint:
            return None
        self._entries.move_to_end(environment_id)
        return entry[1]

"""SSE subscribers per environment, woken by change messages.

On a change the hub reloads the ruleset of each affected environment through the cache, in its own
short session, and offers it to that environment's subscribers when the ETag changed. Each
subscriber queue holds one item, and a newer item replaces a pending one, so a slow consumer only
ever gets the latest ruleset and memory stays bounded.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.models import Environment
from megooflags_api.services.ruleset_cache import CachedRuleset, RulesetCache
from megooflags_api.services.sdk_keys import SdkPrincipal

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Revoked:
    """The key was revoked or its environment deleted: the stream sends `revoked` and closes."""


StreamItem = CachedRuleset | Revoked


class Subscriber:
    def __init__(self, principal: SdkPrincipal) -> None:
        self.principal = principal
        self.queue: asyncio.Queue[StreamItem] = asyncio.Queue(maxsize=1)

    def offer(self, item: StreamItem) -> None:
        """Queue `item`, dropping an older item the stream has not taken yet."""
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(item)


class StreamHub:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        cache: RulesetCache,
        debounce: float = 0.1,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._cache = cache
        self._debounce = debounce
        self._subscribers: dict[uuid.UUID, set[Subscriber]] = {}
        self._last_etag: dict[uuid.UUID, str] = {}
        self._pending: set[uuid.UUID] = set()
        self._flush: asyncio.Task[None] | None = None

    @property
    def subscriber_count(self) -> int:
        return sum(len(subs) for subs in self._subscribers.values())

    def subscribe(self, principal: SdkPrincipal) -> Subscriber:
        sub = Subscriber(principal)
        self._subscribers.setdefault(principal.environment.id, set()).add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        env_id = sub.principal.environment.id
        subs = self._subscribers.get(env_id)
        if subs is None:
            return
        subs.discard(sub)
        if not subs:
            del self._subscribers[env_id]
            self._last_etag.pop(env_id, None)

    def notify_project(self, project_id: uuid.UUID) -> None:
        """Refresh the subscribed environments of `project_id` after `debounce`; bursts merge."""
        env_ids = {
            env_id
            for env_id, subs in self._subscribers.items()
            if next(iter(subs)).principal.environment.project_id == project_id
        }
        if not env_ids:
            return
        self._pending |= env_ids
        if self._flush is None or self._flush.done():
            self._flush = asyncio.create_task(self._run_flush())

    def notify_key_revoked(self, key_id: uuid.UUID) -> None:
        for subs in self._subscribers.values():
            for sub in subs:
                if sub.principal.key_id == key_id:
                    sub.offer(Revoked())

    async def recheck_all(self) -> None:
        """Refresh every subscribed environment; covers change messages that never arrived."""
        await self.refresh(list(self._subscribers))

    async def run_recheck_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.recheck_all()
            except Exception:
                logger.exception("re-checking the open streams failed")

    async def _run_flush(self) -> None:
        while self._pending:
            await asyncio.sleep(self._debounce)
            pending, self._pending = self._pending, set()
            await self.refresh(pending)

    async def refresh(self, env_ids: Iterable[uuid.UUID]) -> None:
        """Offer each environment's ruleset to its subscribers when its ETag changed, or
        `Revoked` when the environment is gone. Errors are logged, never raised."""
        for env_id in env_ids:
            subs = self._subscribers.get(env_id)
            if not subs:
                continue
            environment = next(iter(subs)).principal.environment
            item: StreamItem
            try:
                async with self._sessionmaker() as session:
                    exists = await session.scalar(
                        select(Environment.id).where(Environment.id == env_id)
                    )
                    if exists is None:
                        item = Revoked()
                    else:
                        item = await self._cache.get_entry(session, environment, max_age=0)
            except Exception:
                logger.exception("refreshing the streams of environment %s failed", env_id)
                continue
            if isinstance(item, CachedRuleset):
                if item.etag == self._last_etag.get(env_id):
                    continue
                self._last_etag[env_id] = item.etag
            for sub in list(self._subscribers.get(env_id, ())):
                sub.offer(item)

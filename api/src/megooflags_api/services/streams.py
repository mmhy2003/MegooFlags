"""SSE subscribers per environment, woken by change messages.

On a change the hub reloads the ruleset of each affected environment through its stream source
(the cache, in its own short session, unless a worker supplies its snapshot store), and offers it
to that environment's subscribers when the ETag changed. Each subscriber queue holds one item, and
a newer item replaces a pending one, so a slow consumer only ever gets the latest ruleset and
memory stays bounded.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.services.delivery import DbStreamSource, StreamSource
from megooflags_api.services.ruleset_cache import CachedRuleset, RulesetCache
from megooflags_api.services.sdk_keys import SdkPrincipal

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Revoked:
    """The key was revoked or its environment deleted: the stream sends `revoked` and closes."""


@dataclass(frozen=True, slots=True)
class Closed:
    """The node stops serving streams (a worker draining): the stream closes without a frame, so
    the SDK reconnects, through the load balancer, to another node."""


StreamItem = CachedRuleset | Revoked | Closed


class Subscriber:
    def __init__(self, principal: SdkPrincipal) -> None:
        self.principal = principal
        self.queue: asyncio.Queue[StreamItem] = asyncio.Queue(maxsize=1)
        self._final = False

    def offer(self, item: StreamItem) -> None:
        """Queue `item`, dropping an older item the stream has not taken yet. `Revoked` and
        `Closed` are final: nothing offered after them replaces them, so the stream always
        closes."""
        if self._final:
            return
        self._final = isinstance(item, Revoked | Closed)
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(item)


class StreamHub:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        cache: RulesetCache,
        debounce: float = 0.1,
        *,
        source: StreamSource | None = None,
    ) -> None:
        self._source = source if source is not None else DbStreamSource(sessionmaker, cache)
        self._debounce = debounce
        self._subscribers: dict[uuid.UUID, set[Subscriber]] = {}
        self._last_etag: dict[uuid.UUID, str] = {}
        self._pending: set[uuid.UUID] = set()
        self._flush: asyncio.Task[None] | None = None
        # Refreshes run one at a time, so a slow one never offers a ruleset older than one a
        # concurrent refresh (debounced flush, re-check) already offered.
        self._refresh_lock = asyncio.Lock()

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

    def close_all(self) -> None:
        """End every stream without a frame."""
        self._offer_all(Closed())

    def revoke_all(self) -> None:
        """End every stream with `revoked`."""
        self._offer_all(Revoked())

    def _offer_all(self, item: StreamItem) -> None:
        for subs in self._subscribers.values():
            for sub in subs:
                sub.offer(item)

    async def recheck_all(self) -> None:
        """Close the streams of revoked or expired keys, then refresh every subscribed
        environment; covers change messages that never arrived and keys that expired."""
        if self._subscribers:
            await self._revoke_inactive()
        await self.refresh(list(self._subscribers))

    async def _revoke_inactive(self) -> None:
        subs = [sub for env_subs in self._subscribers.values() for sub in env_subs]
        inactive = await self._source.inactive_keys({sub.principal.key_id for sub in subs})
        for sub in subs:
            if sub.principal.key_id in inactive:
                sub.offer(Revoked())

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
        async with self._refresh_lock:
            for env_id in env_ids:
                await self._refresh_one(env_id)

    async def _refresh_one(self, env_id: uuid.UUID) -> None:
        subs = self._subscribers.get(env_id)
        if not subs:
            return
        environment = next(iter(subs)).principal.environment
        try:
            current = await self._source.current(environment)
        except Exception:
            logger.exception("refreshing the streams of environment %s failed", env_id)
            return
        item: StreamItem = Revoked() if current is None else current
        if isinstance(item, CachedRuleset):
            if item.etag == self._last_etag.get(env_id):
                return
            self._last_etag[env_id] = item.etag
        for sub in list(self._subscribers.get(env_id, ())):
            sub.offer(item)

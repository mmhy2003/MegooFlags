"""The worker streams open on this control-plane process, and the snapshots they are offered.

On a change message, and on the periodic re-check, the hub rebuilds the affected environments'
snapshots one rebuild at a time and keeps the newest per environment, so each version is signed
once and shared by every worker. It offers a `put` only for a version higher than the last one it
offered, and a `remove` for an environment that disappeared. Each worker's queue keeps only the
newest item per environment, so a slow worker gets the latest snapshots and memory stays bounded.
Node messages (drain, resume, rotate, revoke) offer `state` or the final `revoked`.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api import metrics
from megooflags_api.models import Node, NodeToken
from megooflags_api.services.cluster.signing import SnapshotSigner
from megooflags_api.services.cluster.snapshot_format import SignedSnapshot
from megooflags_api.services.cluster.snapshots import build_snapshot, environment_refs
from megooflags_api.services.ruleset_cache import RulesetCache
from megooflags_api.services.sdk_keys import is_inactive

logger = logging.getLogger(__name__)


def _json(data: object) -> bytes:
    return json.dumps(data, separators=(",", ":")).encode()


@dataclass(frozen=True, slots=True)
class StreamEvent:
    event: str
    data: bytes
    environment_id: uuid.UUID | None = None
    version: int | None = None

    def frame(self) -> bytes:
        """The SSE frame; a `put` carries its version as id and one `data:` line per line."""
        head = b"" if self.version is None else b"id: %d\n" % self.version
        data = b"".join(b"data: " + line + b"\n" for line in self.data.split(b"\n"))
        return head + b"event: " + self.event.encode() + b"\n" + data + b"\n"

    @classmethod
    def put(cls, snap: SignedSnapshot) -> "StreamEvent":
        header = snap.header
        return cls("put", snap.to_bytes(), header.environment_id, header.version)

    @classmethod
    def remove(cls, environment_id: uuid.UUID) -> "StreamEvent":
        return cls("remove", _json({"environment_id": str(environment_id)}), environment_id)


REVOKED = StreamEvent("revoked", b"{}")
_STATE = "state"


class NodeStream:
    """One connected worker: pending items, the newest per environment, plus its state."""

    def __init__(self, node_id: uuid.UUID, token_id: uuid.UUID, state: str = "active") -> None:
        self.node_id = node_id
        self.token_id = token_id
        self.state = state
        self._pending: dict[uuid.UUID | str, StreamEvent] = {}
        self._ready = asyncio.Event()
        self._final = False

    def _offer(self, key: uuid.UUID | str, event: StreamEvent) -> None:
        if self._final:
            return
        self._pending.pop(key, None)  # the replacement goes last, after what was offered before
        self._pending[key] = event
        self._ready.set()

    def offer_put(self, snap: SignedSnapshot) -> None:
        self._offer(snap.header.environment_id, StreamEvent.put(snap))

    def offer_remove(self, environment_id: uuid.UUID) -> None:
        self._offer(environment_id, StreamEvent.remove(environment_id))

    def offer_state(self, state: str) -> None:
        self.state = state
        self._offer(_STATE, StreamEvent(_STATE, _json({"state": state})))

    def offer_revoked(self) -> None:
        """Final: drops what is pending, and nothing offered later is sent."""
        if self._final:
            return
        self._pending.clear()
        self._offer("revoked", REVOKED)
        self._final = True

    async def next(
        self,
        timeout: float,  # noqa: ASYNC109 - an idle wait that returns None, the ping interval
    ) -> list[StreamEvent] | None:
        """The pending items in the order offered; None when nothing came within `timeout`."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except TimeoutError:
            return None
        events = list(self._pending.values())
        self._pending.clear()
        self._ready.clear()
        return events


class NodeHub:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        cache: RulesetCache,
        signer: Callable[[], SnapshotSigner],
        debounce: float = 0.1,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._cache = cache
        self._signer = signer
        self._debounce = debounce
        self._streams: set[NodeStream] = set()
        # The newest snapshot per environment, which is also the last one offered.
        self._snapshots: dict[uuid.UUID, SignedSnapshot] = {}
        # True while `_snapshots` holds every environment: kept current only while streams are
        # open, so a change without streams makes the next connect rebuild everything.
        self._complete = False
        self._lock = asyncio.Lock()
        self._pending: set[uuid.UUID] = set()
        self._flush: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def stream_count(self) -> int:
        return len(self._streams)

    def subscribe(
        self, node_id: uuid.UUID, token_id: uuid.UUID, state: str = "active"
    ) -> NodeStream:
        stream = NodeStream(node_id, token_id, state)
        self._streams.add(stream)
        metrics.NODE_STREAMS.inc()
        return stream

    def unsubscribe(self, stream: NodeStream) -> None:
        if stream in self._streams:
            self._streams.discard(stream)
            metrics.NODE_STREAMS.dec()

    async def snapshots(self) -> dict[uuid.UUID, SignedSnapshot]:
        """Every environment's snapshot, built when the hub does not hold them all. Raises when
        they cannot all be built, so a worker never takes a missing environment as removed."""
        async with self._lock:
            if not self._complete:
                await self._rebuild(None)
            if not self._complete:
                raise RuntimeError("the environment snapshots could not be built")
            return dict(self._snapshots)

    def notify_project(self, project_id: uuid.UUID) -> None:
        """Rebuild the environments of `project_id` after `debounce`; bursts merge."""
        if not self._streams:
            self._complete = False
            return
        self._pending.add(project_id)
        if self._flush is None or self._flush.done():
            self._flush = asyncio.create_task(self._run_flush())

    def notify_node(self, node_id: uuid.UUID) -> None:
        """Re-read the node's drain, revoke and token state for its open streams."""
        streams = [s for s in self._streams if s.node_id == node_id]
        if streams:
            task = asyncio.create_task(self._check_streams(streams))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def recheck_all(self) -> None:
        """Rebuild every environment and re-check every node and token: covers change messages
        that never arrived and tokens that expired."""
        if not self._streams:
            self._complete = False
            return
        async with self._lock:
            await self._rebuild(None)
        await self._check_streams(list(self._streams))

    async def run_recheck_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.recheck_all()
            except Exception:
                logger.exception("re-checking the worker streams failed")

    async def _run_flush(self) -> None:
        while self._pending:
            await asyncio.sleep(self._debounce)
            pending, self._pending = self._pending, set()
            async with self._lock:
                for project_id in pending:
                    await self._rebuild(project_id)

    async def _rebuild(self, project_id: uuid.UUID | None) -> None:
        """Rebuild all environments (of one project) and offer what changed. Errors are logged,
        never raised; an environment that failed keeps its last snapshot. Runs under the lock."""
        try:
            async with self._sessionmaker() as session:
                refs = await environment_refs(session, project_id)
        except Exception:
            logger.exception("listing the environments to snapshot failed")
            return
        failed = False
        gone = {
            env_id
            for env_id, snap in self._snapshots.items()
            if project_id is None or snap.header.project_id == project_id
        } - {env.id for env, _ in refs}
        for env, project_key in refs:
            try:
                async with self._sessionmaker() as session:
                    snap = await build_snapshot(
                        session, env, project_key, self._cache, self._signer()
                    )
            except Exception:
                logger.exception("building the snapshot of environment %s failed", env.id)
                failed = True
                continue
            if snap is None:
                gone.add(env.id)
            else:
                self._offer_put(snap)
        self._remove(gone)
        if project_id is None:
            self._complete = not failed

    def _offer_put(self, snap: SignedSnapshot) -> None:
        env_id = snap.header.environment_id
        last = self._snapshots.get(env_id)
        if last is not None and snap.header.version <= last.header.version:
            return
        self._snapshots[env_id] = snap
        for stream in self._streams:
            stream.offer_put(snap)

    def _remove(self, env_ids: Iterable[uuid.UUID]) -> None:
        for env_id in env_ids:
            if self._snapshots.pop(env_id, None) is None:
                continue
            for stream in self._streams:
                stream.offer_remove(env_id)

    async def _check_streams(self, streams: list[NodeStream]) -> None:
        """Offer `revoked` to streams of a revoked or removed node or an inactive token, else
        `state` when drain changed. Errors are logged, never raised."""
        try:
            async with self._sessionmaker() as session:
                now = await session.scalar(select(func.now()))
                rows = await session.execute(
                    select(
                        NodeToken.id,
                        NodeToken.revoked_at,
                        NodeToken.expires_at,
                        Node.revoked_at,
                        Node.drain_requested_at,
                    )
                    .join(Node, Node.id == NodeToken.node_id)
                    .where(NodeToken.id.in_({s.token_id for s in streams}))
                )
                tokens = {row[0]: row[1:] for row in rows}
        except Exception:
            logger.exception("re-checking the worker nodes failed")
            return
        assert now is not None
        for stream in streams:
            row = tokens.get(stream.token_id)
            if row is None:
                stream.offer_revoked()
                continue
            token_revoked_at, expires_at, node_revoked_at, drain_requested_at = row
            if node_revoked_at is not None or is_inactive(token_revoked_at, expires_at, now):
                stream.offer_revoked()
                continue
            state = "active" if drain_requested_at is None else "draining"
            if state != stream.state:
                stream.offer_state(state)

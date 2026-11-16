"""The worker's link to the control plane: the snapshot stream, heartbeats and event batches.

`run` keeps one `POST /api/cluster/v1/stream` open. It sends the versions the worker holds, so
the control plane only sends the snapshots that changed, applies every `put` and `remove` to the
store and tells the SDK stream hub, and marks the store synced on `synced`. When the stream is
lost it reconnects with backoff, and the worker keeps serving what it has. Only a revoked token
stops the loop: the worker then answers SDK requests with 503 until it starts with a valid one.

The stream is read line by line from bytes, splitting on b"\\n" only, so a snapshot's data
lines reach `SignedSnapshot.from_bytes` exactly as they were signed.
"""

import asyncio
import json
import logging
import ssl
import uuid
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx

from megooflags_api import __version__
from megooflags_api.schemas.cluster import ClusterEventsIn, HeartbeatIn, HeartbeatOut, StreamIn
from megooflags_api.services.cluster.snapshot_format import SignedSnapshot, SnapshotError
from megooflags_api.services.worker.buffer import EventBuffer, SendResult
from megooflags_api.services.worker.state import WorkerState
from megooflags_api.services.worker.store import SnapshotStore
from megooflags_api.settings import Settings

logger = logging.getLogger(__name__)

STREAM_PATH = "/api/cluster/v1/stream"
HEARTBEAT_PATH = "/api/cluster/v1/heartbeat"
EVENTS_PATH = "/api/cluster/v1/events"
VERSION_HEADER = "X-MegooFlags-Version"  # as routers/cluster.py reads it
REQUEST_TIMEOUT = 10.0
HELLO_READ_TIMEOUT = 45.0  # until `hello` gives the ping interval
NOT_CONTROL_PLANE_RETRY = 60.0
# The statuses that say the batch itself is bad, so it is dropped. Any other failure, including
# a proxy's 404, 408 or 429 in front of the control plane, keeps it for a later round.
REJECTED_STATUSES = {400, 413, 422}


class SdkStreams(Protocol):
    """What the client needs of the SDK `StreamHub`."""

    @property
    def subscriber_count(self) -> int: ...

    def notify_project(self, project_id: uuid.UUID) -> None: ...

    def close_all(self) -> None: ...

    def revoke_all(self) -> None: ...


def tls_verify(settings: Settings) -> ssl.SSLContext | bool:
    """A context trusting `control_plane_ca_file` when one is set, else the system CAs."""
    if settings.control_plane_ca_file:
        return ssl.create_default_context(cafile=settings.control_plane_ca_file)
    return True


def _error(response: httpx.Response) -> tuple[str | None, str]:
    """The `code` and `message` of an error body."""
    try:
        body: Any = response.json()
    except ValueError:
        return None, response.text[:200]
    if not isinstance(body, dict):
        return None, response.text[:200]
    code = body.get("code")
    return code if isinstance(code, str) else None, str(body.get("message", ""))


async def _lines(response: httpx.Response) -> AsyncIterator[bytes]:
    pending = bytearray()
    scanned = 0
    async for chunk in response.aiter_bytes():
        pending += chunk
        while (end := pending.find(b"\n", scanned)) != -1:
            yield bytes(pending[:end]).removesuffix(b"\r")
            del pending[: end + 1]
            scanned = 0
        scanned = len(pending)


class ControlPlaneClient:
    heartbeat_seconds = 10.0
    backoff_min = 0.5
    backoff_max = 30.0
    incompatible_retry_seconds = 60.0
    version = __version__

    def __init__(
        self,
        settings: Settings,
        store: SnapshotStore,
        state: WorkerState,
        hub: SdkStreams,
        buffer: EventBuffer,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.control_plane_url is None or settings.node_token is None:
            raise ValueError(
                "a worker needs MEGOOFLAGS_CONTROL_PLANE_URL and MEGOOFLAGS_NODE_TOKEN"
            )
        self._settings = settings
        self._store = store
        self._state = state
        self._hub = hub
        self._buffer = buffer
        self._http = httpx.AsyncClient(
            base_url=str(settings.control_plane_url),
            headers={"Authorization": f"Bearer {settings.node_token.get_secret_value()}"},
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
            verify=tls_verify(settings),
            transport=transport,
        )
        self.read_timeout = HELLO_READ_TIMEOUT
        self._delay = self.backoff_min
        self._drain_task: asyncio.Task[None] | None = None
        # Environments held under the control plane's previous signing key, until re-signed.
        self._rekeying: set[uuid.UUID] = set()

    async def run(self) -> None:
        """Keep the snapshot stream open until the node is revoked."""
        self._delay = self.backoff_min
        while True:
            try:
                wait = await self._sync()
            except (httpx.TransportError, TimeoutError) as exc:
                logger.warning("lost the control plane: %s", exc or type(exc).__name__)
                wait = self._backoff()
            except Exception:
                logger.exception("the control plane stream failed")
                wait = self._backoff()
            finally:
                self._state.connected = False
            if wait is None:
                return
            await self._sleep(wait)

    async def heartbeat_loop(self) -> None:
        while True:
            if not self._store.meta.revoked:
                try:
                    await self.heartbeat()
                except httpx.TransportError as exc:
                    logger.debug("heartbeat failed: %s", exc)
                except Exception as exc:
                    logger.warning("heartbeat failed: %s", exc)
            await asyncio.sleep(self.heartbeat_seconds)

    async def heartbeat(self) -> None:
        """Report this worker's state, and take the state the control plane answers."""
        age = self._state.snapshot_age()
        body = HeartbeatIn(
            version=self.version,
            snapshot_version=self._store.max_version(),
            snapshot_age_seconds=None if age is None else max(age, 0.0),
            sdk_streams=self._hub.subscriber_count,
            buffered_events=self._buffer.pending_events,
            ready=self._state.unavailable_reason() is None,
        )
        response = await self._http.post(
            HEARTBEAT_PATH, json=body.model_dump(mode="json"), headers=self._headers()
        )
        response.raise_for_status()
        out = HeartbeatOut.model_validate_json(response.content)
        self._set_draining(out.state == "draining")

    async def send_events(self, batch: ClusterEventsIn) -> SendResult:
        """Forward one buffered batch; "retry" keeps it for a later round."""
        try:
            response = await self._http.post(
                EVENTS_PATH, json=batch.model_dump(mode="json"), headers=self._headers()
            )
        except httpx.TransportError as exc:
            logger.debug("sending events failed: %s", exc)
            return "retry"
        status = response.status_code
        if 200 <= status < 300:
            return "accepted"
        if status in REJECTED_STATUSES:
            return "rejected"
        return "retry"

    async def aclose(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            self._drain_task = None
        await self._http.aclose()

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def _headers(self) -> dict[str, str]:
        return {VERSION_HEADER: self.version}

    def _backoff(self) -> float:
        delay = self._delay
        self._delay = min(delay * 2, self.backoff_max)
        return delay

    async def _sync(self) -> float | None:
        """One stream: the seconds to wait before the next, or None to stop."""
        versions = {
            env_id: version
            for env_id, version in self._store.versions().items()
            if env_id not in self._rekeying
        }
        body = StreamIn(versions=versions, public_key=self._store.meta.public_key)
        self.read_timeout = HELLO_READ_TIMEOUT
        async with self._http.stream(
            "POST",
            STREAM_PATH,
            json=body.model_dump(mode="json"),
            headers=self._headers(),
            # Reads are timed per line below, as the limit changes once `hello` arrives.
            timeout=httpx.Timeout(REQUEST_TIMEOUT, read=None),
        ) as response:
            if response.status_code != 200:
                await response.aread()
                return self._refused(response)
            self._started()
            if await self._consume(response):
                return None
        logger.warning("the control plane closed the stream")
        return self._backoff()

    def _refused(self, response: httpx.Response) -> float | None:
        status = response.status_code
        code, message = _error(response)
        if status == 403 and code == "node_revoked":
            logger.error("the control plane revoked this node: %s", message)
            self._revoke()
            return None
        if status == 409 and code == "incompatible_version":
            logger.error("%s", message)
            self._state.incompatible = message
            return self.incompatible_retry_seconds
        if status == 409 and code == "not_control_plane":
            logger.error("%s", message)
            return NOT_CONTROL_PLANE_RETRY
        logger.warning("the control plane refused the stream: %d %s", status, message)
        return self._backoff()

    def _started(self) -> None:
        self._state.incompatible = None
        if self._store.meta.revoked:
            logger.info("the control plane accepted this node's token again")
            self._store.meta.revoked = False
            self._store.save_meta()

    async def _consume(self, response: httpx.Response) -> bool:
        """Handle the events of one stream; True after `revoked`."""
        lines = _lines(response)
        event = b""
        data: list[bytes] = []
        while True:
            try:
                async with asyncio.timeout(self.read_timeout):
                    line = await anext(lines)
            except StopAsyncIteration:
                return False
            if not line:
                if (event or data) and await self._dispatch(
                    event.decode() or "message", b"\n".join(data)
                ):
                    return True
                event, data = b"", []
            elif line.startswith(b":"):
                self._state.touch()
            else:
                name, _, value = line.partition(b":")
                value = value.removeprefix(b" ")
                if name == b"event":
                    event = value
                elif name == b"data":
                    data.append(value)

    async def _dispatch(self, event: str, data: bytes) -> bool:
        self._state.touch()
        if event == "hello":
            await self._hello(json.loads(data))
        elif event == "put":
            await self._put(data)
        elif event == "remove":
            environment_id = uuid.UUID(json.loads(data)["environment_id"])
            ref = await asyncio.to_thread(self._store.remove, environment_id)
            if ref is not None:
                self._hub.notify_project(ref.project_id)
        elif event == "synced":
            self._rekeying.clear()
            self._store.mark_synced()
            self._delay = self.backoff_min
        elif event == "state":
            self._set_draining(json.loads(data)["state"] == "draining")
        elif event == "revoked":
            logger.error("the control plane revoked this node")
            self._revoke()
            return True
        return False

    async def _hello(self, hello: dict[str, Any]) -> None:
        meta = self._store.meta
        public_key = str(hello["public_key"])
        if meta.public_key is not None and meta.public_key != public_key:
            # The control plane's secret key changed: it sends every snapshot again, re-signed
            # with the versions the store holds. The old ones keep serving until replaced.
            logger.warning("the control plane's signing key changed; resyncing every snapshot")
            self._rekeying = set(self._store.versions())
        meta.public_key = public_key
        meta.persist_context = bool(hello["persist_context"])
        meta.control_plane_version = str(hello["version"])
        self._store.save_meta()
        self.read_timeout = 3 * float(hello["ping_seconds"])
        self._set_draining(hello["state"] == "draining")
        self._state.connected = True

    async def _put(self, data: bytes) -> None:
        try:
            snap = SignedSnapshot.from_bytes(data)
        except SnapshotError as exc:
            logger.warning("ignoring a malformed snapshot: %s", exc)
            return
        header = snap.header
        replace_equal = header.environment_id in self._rekeying
        if await asyncio.to_thread(self._store.apply, snap, replace_equal=replace_equal):
            self._rekeying.discard(header.environment_id)
            self._hub.notify_project(header.project_id)
        else:
            logger.warning(
                "ignoring snapshot %d of %s: it is not newer or does not verify",
                header.version,
                header.environment_id,
            )

    def _revoke(self) -> None:
        self._store.meta.revoked = True
        self._store.save_meta()
        self._set_draining(False)
        self._hub.revoke_all()

    def _set_draining(self, draining: bool) -> None:
        if draining == self._state.draining:
            return
        self._state.draining = draining
        if self._drain_task is not None:
            self._drain_task.cancel()
            self._drain_task = None
        if draining:
            logger.info(
                "draining: SDK streams close in %ss", self._settings.worker_drain_grace_seconds
            )
            self._drain_task = asyncio.create_task(self._close_after_grace())

    async def _close_after_grace(self) -> None:
        await asyncio.sleep(self._settings.worker_drain_grace_seconds)
        if self._state.draining:
            self._hub.close_all()

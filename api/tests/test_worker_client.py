import asyncio
import json
import ssl
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from megooflags_api.schemas.cluster import ClusterEventsIn
from megooflags_api.services.cluster.node_hub import REVOKED, StreamEvent
from megooflags_api.services.cluster.signing import SnapshotSigner
from megooflags_api.services.cluster.snapshot_format import (
    FORMAT,
    SignedSnapshot,
    SnapshotHeader,
)
from megooflags_api.services.worker.buffer import EventBuffer
from megooflags_api.services.worker.client import ControlPlaneClient, tls_verify
from megooflags_api.services.worker.state import WorkerState
from megooflags_api.services.worker.store import SnapshotStore
from megooflags_api.settings import Settings

SIGNER = SnapshotSigner("s" * 32)
OTHER_SIGNER = SnapshotSigner("o" * 32)
PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a0")
ENV_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ENV_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
TOKEN = "mf_node_" + "a" * 32
STREAM = "/api/cluster/v1/stream"


def ruleset(environment: str) -> bytes:
    data = {
        "schema_version": 1,
        "project": "web",
        "environment": environment,
        "flags": {},
        "segments": {},
    }
    return json.dumps(data, separators=(",", ":")).encode()


def snapshot(
    environment_id: uuid.UUID = ENV_A, version: int = 1, signer: SnapshotSigner = SIGNER
) -> SignedSnapshot:
    header = SnapshotHeader(
        format=FORMAT,
        environment_id=environment_id,
        environment_key=str(environment_id)[-1],
        project_id=PROJECT_ID,
        project_key="web",
        version=version,
        published_at=datetime(2026, 11, 16, 12, 0, tzinfo=UTC),
        keys=(),
    )
    body = ruleset(header.environment_key)
    return SignedSnapshot(header, body, signer.sign(header.to_json() + b"\n" + body))


def hello(state: str = "active", ping_seconds: float = 2, public_key: str | None = None) -> bytes:
    data = {
        "version": "0.9.0",
        "public_key": SIGNER.public_key if public_key is None else public_key,
        "persist_context": True,
        "state": state,
        "ping_seconds": ping_seconds,
    }
    return StreamEvent("hello", json.dumps(data).encode()).frame()


def synced(version: int) -> bytes:
    return StreamEvent("synced", json.dumps({"snapshot_version": version}).encode()).frame()


def state_event(state: str) -> bytes:
    return StreamEvent("state", json.dumps({"state": state}).encode()).frame()


def error(status: int, code: str, message: str = "nope") -> httpx.Response:
    return httpx.Response(status, json={"code": code, "message": message, "details": None})


Reply = list[bytes] | httpx.Response | Exception


class ControlPlane:
    """A MockTransport handler: each stream request takes the next reply; once they run out the
    request never answers. A list of frames is streamed, then the stream stays open unless
    `close` is set."""

    def __init__(self, *replies: Reply, close: bool = False) -> None:
        self.replies = list(replies)
        self.close = close
        self.requests: list[httpx.Request] = []
        self.other: Callable[[httpx.Request], httpx.Response] = lambda _: httpx.Response(404)

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != STREAM:
            return self.other(request)
        self.requests.append(request)
        if not self.replies:
            await asyncio.Event().wait()
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=self._frames(reply)
        )

    async def _frames(self, frames: list[bytes]) -> AsyncIterator[bytes]:
        for frame in frames:
            yield frame
        if not self.close:
            await asyncio.Event().wait()


class FakeHub:
    def __init__(self) -> None:
        self.notified: list[uuid.UUID] = []
        self.calls: list[str] = []
        self.subscriber_count = 3

    def notify_project(self, project_id: uuid.UUID) -> None:
        self.notified.append(project_id)

    def close_all(self) -> None:
        self.calls.append("close_all")

    def revoke_all(self) -> None:
        self.calls.append("revoke_all")


def settings(tmp_path: Path, **values: object) -> Settings:
    defaults: dict[str, object] = {
        "role": "worker",
        "control_plane_url": "http://cp.test",
        "node_token": TOKEN,
        "control_plane_insecure": True,
        "data_dir": tmp_path,
        "worker_drain_grace_seconds": 0.05,
    }
    return Settings(**(defaults | values))  # type: ignore[arg-type]


class Worker:
    def __init__(self, tmp_path: Path, cp: ControlPlane, **values: object) -> None:
        self.store = SnapshotStore(tmp_path)
        self.store.load()
        self.state = WorkerState(self.store)
        self.hub = FakeHub()
        self.buffer = EventBuffer(tmp_path / "buffer", max_bytes=1 << 20)
        self.buffer.load()
        self.client = ControlPlaneClient(
            settings(tmp_path, **values),
            self.store,
            self.state,
            self.hub,
            self.buffer,
            transport=httpx.MockTransport(cp.handler),
        )
        self.client.version = "0.9.1"
        self.client.backoff_min = 0.01
        self.client.backoff_max = 0.04
        self.sleeps: list[float] = []
        self.incompatible: list[str | None] = []  # `state.incompatible` during each sleep
        self.reasons: list[str | None] = []  # `state.unavailable_reason()` during each sleep
        real_sleep = self.client._sleep

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            self.incompatible.append(self.state.incompatible)
            self.reasons.append(self.state.unavailable_reason())
            await real_sleep(0.001)

        self.client._sleep = sleep  # type: ignore[method-assign]
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.client.run())

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.client.aclose()


async def until(condition: Callable[[], bool]) -> None:
    """Poll `condition` (state the client changes from its own task) for up to 2 s."""
    async with asyncio.timeout(2):
        while not condition():  # noqa: ASYNC110
            await asyncio.sleep(0.005)


@pytest.fixture
async def make(tmp_path: Path) -> AsyncIterator[Callable[..., Worker]]:
    workers: list[Worker] = []

    def build(cp: ControlPlane, **values: object) -> Worker:
        worker = Worker(tmp_path, cp, **values)
        workers.append(worker)
        return worker

    yield build
    for worker in workers:
        await worker.stop()


def test_state_readiness_matrix(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.load()
    state = WorkerState(store)
    assert state.unavailable_reason() == "not_ready"
    assert state.readiness() == "not_ready"

    store.mark_synced()
    assert state.unavailable_reason() is None
    assert state.readiness() is None

    store.valid = False
    assert state.readiness() == "not_ready"
    store.valid = True

    state.draining = True
    assert state.unavailable_reason() is None
    assert state.readiness() == "draining"

    store.meta.revoked = True
    assert state.unavailable_reason() == "revoked"
    assert state.readiness() == "revoked"


def test_state_touch_saves_at_most_every_10_seconds(tmp_path: Path) -> None:
    now = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    store = SnapshotStore(tmp_path)
    store.load()
    state = WorkerState(store, clock=lambda: now)
    assert state.snapshot_age() is None

    state.touch()
    saved = json.loads((tmp_path / "cluster.json").read_bytes())
    assert saved["last_contact_at"] == now.isoformat()

    first = now
    now += timedelta(seconds=5)
    state.touch()
    assert store.meta.last_contact_at == now
    saved = json.loads((tmp_path / "cluster.json").read_bytes())
    assert saved["last_contact_at"] == first.isoformat()

    now += timedelta(seconds=6)
    state.touch()
    saved = json.loads((tmp_path / "cluster.json").read_bytes())
    assert saved["last_contact_at"] == now.isoformat()

    now += timedelta(seconds=4)
    assert state.snapshot_age() == 4.0


async def test_sync_applies_puts_and_marks_synced(make: Callable[..., Worker]) -> None:
    cp = ControlPlane(
        [
            b"retry: 3000\n\n",
            hello(ping_seconds=2),
            StreamEvent.put(snapshot(ENV_A, 4)).frame(),
            StreamEvent.put(snapshot(ENV_B, 5)).frame(),
            synced(5),
        ]
    )
    worker = make(cp)
    worker.store.meta.public_key = SIGNER.public_key
    worker.store.meta.revoked = True
    assert worker.client.read_timeout == 45.0
    worker.start()

    await until(lambda: worker.store.meta.synced)

    request = cp.requests[0]
    assert request.method == "POST"
    assert request.url == "http://cp.test/api/cluster/v1/stream"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["x-megooflags-version"] == "0.9.1"
    assert json.loads(request.content) == {"versions": {}, "public_key": SIGNER.public_key}
    assert worker.store.versions() == {ENV_A: 4, ENV_B: 5}
    assert worker.hub.notified == [PROJECT_ID, PROJECT_ID]
    assert worker.state.connected
    assert worker.state.unavailable_reason() is None  # a successful start clears revoked
    assert worker.store.meta.persist_context is True
    assert worker.store.meta.control_plane_version == "0.9.0"
    assert worker.store.meta.last_contact_at is not None
    assert worker.client.read_timeout == 6.0


async def test_sync_sends_held_versions(make: Callable[..., Worker]) -> None:
    cp = ControlPlane([hello(), synced(3)])
    worker = make(cp)
    worker.store.meta.public_key = SIGNER.public_key
    assert worker.store.apply(snapshot(ENV_A, 3))
    worker.start()

    await until(lambda: worker.store.meta.synced)

    body = json.loads(cp.requests[0].content)
    assert body == {"versions": {str(ENV_A): 3}, "public_key": SIGNER.public_key}


async def test_new_public_key_replaces_snapshots(make: Callable[..., Worker]) -> None:
    cp = ControlPlane(
        [
            hello(public_key=OTHER_SIGNER.public_key),
            StreamEvent.put(snapshot(ENV_A, 3, signer=OTHER_SIGNER)).frame(),
            synced(3),
        ]
    )
    worker = make(cp)
    worker.store.meta.public_key = SIGNER.public_key
    assert worker.store.apply(snapshot(ENV_A, 3))
    worker.store.mark_synced()
    worker.start()

    await until(lambda: worker.hub.notified == [PROJECT_ID])

    assert worker.store.meta.public_key == OTHER_SIGNER.public_key
    assert worker.store.versions() == {ENV_A: 3}
    reopened = SnapshotStore(worker.store._dir)
    reopened.load()
    assert reopened.valid and reopened.versions() == {ENV_A: 3}


async def test_new_public_key_keeps_serving_if_resync_is_cut(
    make: Callable[..., Worker],
) -> None:
    cp = ControlPlane(
        [hello(public_key=OTHER_SIGNER.public_key)],
        [
            hello(public_key=OTHER_SIGNER.public_key),
            StreamEvent.put(snapshot(ENV_A, 3, signer=OTHER_SIGNER)).frame(),
            synced(3),
        ],
        close=True,
    )
    worker = make(cp)
    worker.store.meta.public_key = SIGNER.public_key
    assert worker.store.apply(snapshot(ENV_A, 3))
    worker.store.mark_synced()
    worker.start()

    await until(lambda: len(cp.requests) == 2)
    # The first stream ended before the put: the old snapshot is still served.
    assert worker.reasons[0] is None
    # Reconnecting, the snapshot not yet re-signed is not reported as held.
    assert json.loads(cp.requests[1].content) == {
        "versions": {},
        "public_key": OTHER_SIGNER.public_key,
    }
    await until(lambda: len(cp.requests) == 3)
    assert worker.hub.notified == [PROJECT_ID]
    assert worker.store.versions() == {ENV_A: 3}
    assert json.loads(cp.requests[2].content)["versions"] == {str(ENV_A): 3}


async def test_backoff_grows_when_stream_ends_before_synced(
    make: Callable[..., Worker],
) -> None:
    cp = ControlPlane([hello()], [hello()], [hello()], close=True)
    worker = make(cp)
    worker.start()

    await until(lambda: len(cp.requests) == 4)

    assert worker.sleeps == [0.01, 0.02, 0.04]


async def test_bad_signature_put_ignored(make: Callable[..., Worker]) -> None:
    cp = ControlPlane(
        [
            hello(),
            StreamEvent.put(snapshot(ENV_A, 4, signer=OTHER_SIGNER)).frame(),
            StreamEvent("put", b"not\na snapshot").frame(),
            StreamEvent.put(snapshot(ENV_B, 5)).frame(),
            synced(5),
        ]
    )
    worker = make(cp)
    worker.start()

    await until(lambda: worker.store.meta.synced)

    assert worker.store.versions() == {ENV_B: 5}
    assert worker.hub.notified == [PROJECT_ID]


async def test_remove_notifies_hub(make: Callable[..., Worker]) -> None:
    other = uuid.UUID(int=99)
    cp = ControlPlane(
        [
            hello(),
            StreamEvent.remove(ENV_A).frame(),
            StreamEvent.remove(other).frame(),
            synced(0),
        ]
    )
    worker = make(cp)
    worker.store.meta.public_key = SIGNER.public_key
    assert worker.store.apply(snapshot(ENV_A, 3))
    worker.start()

    await until(lambda: worker.store.meta.synced)

    assert worker.store.versions() == {}
    assert worker.hub.notified == [PROJECT_ID]


async def test_state_draining_then_close_all_after_grace(make: Callable[..., Worker]) -> None:
    cp = ControlPlane([hello(), synced(0), state_event("draining")])
    worker = make(cp, worker_drain_grace_seconds=0.1)
    worker.start()

    await until(lambda: worker.state.draining)
    assert worker.state.readiness() == "draining"
    assert worker.hub.calls == []

    await until(lambda: worker.hub.calls == ["close_all"])


async def test_resume_before_grace_keeps_streams(make: Callable[..., Worker]) -> None:
    cp = ControlPlane([hello(state="draining"), synced(0), state_event("active")])
    worker = make(cp, worker_drain_grace_seconds=0.1)
    worker.start()

    await until(lambda: worker.store.meta.synced and not worker.state.draining)
    await asyncio.sleep(0.2)

    assert worker.hub.calls == []


async def test_revoked_event_marks_revoked_and_stops(make: Callable[..., Worker]) -> None:
    cp = ControlPlane([hello(), synced(0), REVOKED.frame()], close=True)
    worker = make(cp)
    worker.start()
    assert worker.task is not None

    await asyncio.wait_for(worker.task, 2)

    assert worker.store.meta.revoked
    assert worker.state.unavailable_reason() == "revoked"
    assert not worker.state.connected
    assert worker.hub.calls == ["revoke_all"]
    assert json.loads((worker.store._dir / "cluster.json").read_bytes())["revoked"] is True
    assert len(cp.requests) == 1


async def test_403_node_revoked_stops(make: Callable[..., Worker]) -> None:
    cp = ControlPlane(error(403, "node_revoked"))
    worker = make(cp)
    worker.start()
    assert worker.task is not None

    await asyncio.wait_for(worker.task, 2)

    assert worker.store.meta.revoked
    assert worker.hub.calls == ["revoke_all"]
    assert len(cp.requests) == 1


async def test_409_incompatible_retries_after_delay(make: Callable[..., Worker]) -> None:
    message = "worker 0.9.1 cannot join control plane 0.8.0: different minor"
    cp = ControlPlane(error(409, "incompatible_version", message), [hello(), synced(0)])
    worker = make(cp)
    worker.client.incompatible_retry_seconds = 7.0
    worker.start()

    await until(lambda: worker.store.meta.synced)

    assert worker.sleeps == [7.0]
    assert worker.incompatible == [message]
    assert worker.state.incompatible is None
    assert len(cp.requests) == 2


async def test_409_not_control_plane_retries_after_delay(make: Callable[..., Worker]) -> None:
    cp = ControlPlane(error(409, "not_control_plane"), [hello(), synced(0)])
    worker = make(cp)
    worker.start()

    await until(lambda: worker.store.meta.synced)

    assert worker.sleeps == [60.0]
    assert worker.state.incompatible is None


async def test_reconnect_with_backoff_after_network_error(make: Callable[..., Worker]) -> None:
    cp = ControlPlane(
        httpx.ConnectError("refused"),
        error(503, "unavailable"),
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
        [hello(), synced(0)],
        close=True,
    )
    worker = make(cp)
    worker.start()

    await until(lambda: worker.store.meta.synced)
    # The stream then ends: after a successful hello the backoff starts again from the minimum.
    await until(lambda: len(cp.requests) == 6)

    assert worker.sleeps == [0.01, 0.02, 0.04, 0.04, 0.01]
    assert not worker.state.connected


async def test_stream_silent_past_read_timeout_reconnects(make: Callable[..., Worker]) -> None:
    cp = ControlPlane([hello(ping_seconds=0.01)], [hello()])
    worker = make(cp)
    worker.start()

    await until(lambda: len(cp.requests) == 2)

    assert worker.sleeps == [0.01]


async def test_heartbeat_body(make: Callable[..., Worker]) -> None:
    cp = ControlPlane()
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"state": "draining"})

    cp.other = answer
    worker = make(cp)
    worker.store.meta.public_key = SIGNER.public_key
    assert worker.store.apply(snapshot(ENV_A, 7))
    worker.store.mark_synced()
    worker.state.touch()
    worker.buffer.add_counts(ENV_A, [("new-ui", "on")])

    await worker.client.heartbeat()

    request = seen[0]
    assert request.url == "http://cp.test/api/cluster/v1/heartbeat"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["x-megooflags-version"] == "0.9.1"
    body = json.loads(request.content)
    age = body.pop("snapshot_age_seconds")
    assert 0 <= age < 5
    assert body == {
        "version": "0.9.1",
        "snapshot_version": 7,
        "sdk_streams": 3,
        "buffered_events": 1,
        "ready": True,
    }
    assert worker.state.draining


async def test_heartbeat_loop_survives_errors(make: Callable[..., Worker]) -> None:
    cp = ControlPlane()
    calls: list[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(500)

    cp.other = answer
    worker = make(cp)
    worker.client.heartbeat_seconds = 0.01
    task = asyncio.create_task(worker.client.heartbeat_loop())
    try:
        await until(lambda: len(calls) >= 3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (httpx.Response(202), "accepted"),
        (httpx.Response(422), "rejected"),
        (httpx.Response(400), "rejected"),
        (httpx.Response(503), "retry"),
        (httpx.Response(401), "retry"),
        (httpx.Response(404), "retry"),
        (httpx.Response(408), "retry"),
        (httpx.Response(429), "retry"),
        (httpx.Response(413), "rejected"),
        (httpx.Response(403), "retry"),
        (httpx.Response(409), "retry"),
        (httpx.ConnectError("refused"), "retry"),
    ],
)
async def test_send_events_results(
    make: Callable[..., Worker], reply: httpx.Response | Exception, expected: str
) -> None:
    cp = ControlPlane()
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(reply, Exception):
            raise reply
        return reply

    cp.other = answer
    worker = make(cp)
    batch = ClusterEventsIn(batch_id=uuid.uuid4(), environment_id=ENV_A)

    assert await worker.client.send_events(batch) == expected

    assert seen[0].url == "http://cp.test/api/cluster/v1/events"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert json.loads(seen[0].content)["batch_id"] == str(batch.batch_id)


def _ca_file(path: Path) -> Path:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MegooFlags test CA")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path


def test_tls_settings(tmp_path: Path) -> None:
    secure = {"control_plane_url": "https://cp.test", "control_plane_insecure": False}
    assert tls_verify(settings(tmp_path, **secure)) is True

    ca = _ca_file(tmp_path / "ca.pem")
    verify = tls_verify(settings(tmp_path, control_plane_ca_file=str(ca), **secure))

    assert isinstance(verify, ssl.SSLContext)
    assert verify.verify_mode == ssl.CERT_REQUIRED
    subjects = [cert["subject"] for cert in verify.get_ca_certs()]
    assert ((("commonName", "MegooFlags test CA"),),) in subjects

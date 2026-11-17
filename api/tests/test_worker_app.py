import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from prometheus_client import REGISTRY

from megooflags_api import __version__
from megooflags_api.main import create_app
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.services.cluster.signing import SnapshotSigner
from megooflags_api.services.cluster.snapshot_format import (
    FORMAT,
    SignedSnapshot,
    SnapshotHeader,
    SnapshotKey,
)
from megooflags_api.services.sdk_keys import hash_key
from megooflags_api.services.worker.store import ClusterMeta, write_atomic
from megooflags_api.settings import Settings

SIGNER = SnapshotSigner("s" * 32)
PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a0")
ENV_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SERVER_KEY = "mf_server_" + "a" * 32
TOKEN = "mf_node_" + "a" * 32
RULESET = "/sdk/v1/ruleset"


def ruleset() -> bytes:
    data = {
        "schema_version": 1,
        "project": "web",
        "environment": "production",
        "flags": {},
        "segments": {},
    }
    return json.dumps(data, separators=(",", ":")).encode()


def snapshot(version: int = 7) -> SignedSnapshot:
    key = SnapshotKey(
        id=uuid.UUID(int=1),
        kind=SdkKeyKind.SERVER,
        hash=hash_key(SERVER_KEY),
        revoked_at=None,
        expires_at=None,
    )
    header = SnapshotHeader(
        format=FORMAT,
        environment_id=ENV_ID,
        environment_key="production",
        project_id=PROJECT_ID,
        project_key="web",
        version=version,
        published_at=datetime(2026, 11, 16, 12, 0, tzinfo=UTC),
        keys=(key,),
    )
    body = ruleset()
    return SignedSnapshot(header, body, SIGNER.sign(header.to_json() + b"\n" + body))


def write_disk(data_dir: Path, *, revoked: bool = False) -> SignedSnapshot:
    """What an earlier run left: one signed snapshot and a synced cluster.json."""
    snap = snapshot()
    (data_dir / "snapshots").mkdir(parents=True)
    write_atomic(data_dir / "snapshots" / f"{ENV_ID}.snap", snap.to_bytes())
    meta = ClusterMeta(
        public_key=SIGNER.public_key,
        synced=True,
        revoked=revoked,
        last_contact_at=datetime.now(UTC) - timedelta(seconds=60),
        control_plane_version=__version__,
    )
    write_atomic(data_dir / "cluster.json", meta.to_json())
    return snap


def refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def worker_settings(data_dir: Path, **changes: Any) -> Settings:
    values: dict[str, Any] = {
        "role": "worker",
        "control_plane_url": "https://cp.example.com",
        "node_token": TOKEN,
        "data_dir": data_dir,
        "secret_key": None,
        "database_url": "postgresql+asyncpg://nobody@127.0.0.1:1/none",
        "valkey_url": "redis://127.0.0.1:1/0",
    }
    values.update(changes)
    return Settings(**values)


WorkerFactory = Callable[..., AbstractAsyncContextManager[tuple[FastAPI, httpx.AsyncClient]]]


@pytest.fixture
def worker(tmp_path: Path) -> WorkerFactory:
    @asynccontextmanager
    async def factory(**changes: Any) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
        settings = worker_settings(tmp_path, **changes)
        app = create_app(settings, cluster_transport=httpx.MockTransport(refuse))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://worker") as client:
                yield app, client

    return factory


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_worker_has_no_api_routes(worker: WorkerFactory) -> None:
    async with worker() as (app, client):
        response = await client.get("/api/projects")

    assert response.status_code == 404
    for name in ("engine", "sessionmaker", "valkey", "change_bus"):
        assert not hasattr(app.state, name)


async def test_healthz_reports_role(worker: WorkerFactory) -> None:
    async with worker() as (_, client):
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "role": "worker"}


async def test_not_ready_without_snapshot_and_sdk_503(worker: WorkerFactory) -> None:
    async with worker() as (_, client):
        ready = await client.get("/readyz")
        sdk = await client.get(RULESET, headers=bearer(SERVER_KEY))
        ofrep = await client.post(
            "/ofrep/v1/evaluate/flags", headers=bearer(SERVER_KEY), json={"context": {}}
        )

    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready"}
    assert sdk.status_code == 503
    assert sdk.json()["code"] == "not_ready"
    assert sdk.json()["message"] == "this worker has no valid snapshot yet"
    assert ofrep.status_code == 503
    assert ofrep.json()["code"] == "not_ready"


async def test_ready_from_disk_without_control_plane(worker: WorkerFactory, tmp_path: Path) -> None:
    snap = write_disk(tmp_path)

    async with worker() as (_, client):
        ready = await client.get("/readyz")
        sdk = await client.get(RULESET, headers=bearer(SERVER_KEY))

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert sdk.status_code == 200
    assert sdk.content == snap.ruleset


async def test_revoked_on_disk_serves_503_revoked(worker: WorkerFactory, tmp_path: Path) -> None:
    write_disk(tmp_path, revoked=True)

    async with worker() as (_, client):
        ready = await client.get("/readyz")
        sdk = await client.get(RULESET, headers=bearer(SERVER_KEY))

    assert ready.status_code == 503
    assert ready.json() == {"status": "revoked"}
    assert sdk.status_code == 503
    assert sdk.json()["code"] == "revoked"
    assert sdk.json()["message"] == "this worker's node token was revoked"


async def test_draining_readyz_503_but_serves(worker: WorkerFactory, tmp_path: Path) -> None:
    write_disk(tmp_path)

    async with worker() as (app, client):
        app.state.worker_state.draining = True
        ready = await client.get("/readyz")
        sdk = await client.get(RULESET, headers=bearer(SERVER_KEY))

    assert ready.status_code == 503
    assert ready.json() == {"status": "draining"}
    assert sdk.status_code == 200


def sample(name: str) -> float | None:
    return REGISTRY.get_sample_value(name)


async def test_metrics_exposes_worker_gauges(worker: WorkerFactory, tmp_path: Path) -> None:
    write_disk(tmp_path)

    async with worker() as (app, client):
        app.state.event_buffer.add_counts(ENV_ID, [("new-ui", "on"), ("new-ui", "off")])
        response = await client.get("/metrics")

    assert response.status_code == 200
    for name in (
        "megooflags_worker_snapshot_age_seconds",
        "megooflags_worker_snapshot_version",
        "megooflags_worker_control_plane_connected",
        "megooflags_worker_buffered_events",
        "megooflags_worker_events_dropped_total",
    ):
        assert name in response.text
    assert sample("megooflags_worker_snapshot_version") == 7
    assert sample("megooflags_worker_control_plane_connected") == 0
    assert sample("megooflags_worker_buffered_events") == 2
    age = sample("megooflags_worker_snapshot_age_seconds")
    assert age is not None and 59 <= age < 120


async def test_lifespan_refuses_bad_config(tmp_path: Path) -> None:
    settings = worker_settings(tmp_path, control_plane_url=None, node_token=None)
    app = create_app(settings, cluster_transport=httpx.MockTransport(refuse))

    with pytest.raises(RuntimeError) as exc:
        async with app.router.lifespan_context(app):
            pass

    assert "MEGOOFLAGS_CONTROL_PLANE_URL is not set" in str(exc.value)
    assert "MEGOOFLAGS_NODE_TOKEN is not set" in str(exc.value)

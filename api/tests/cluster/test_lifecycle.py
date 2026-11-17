import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import close_app
from helpers import (
    assert_error,
    bearer,
    create_sdk_key,
    reset_database,
    running,
)
from pydantic import SecretStr
from sqlalchemy import func, update
from test_sdk import RULESET

from cluster.cluster_harness import (
    Cluster,
    CuttableTransport,
    Worker,
    control_plane_settings,
    eventually,
    worker_settings,
)
from cluster.test_sync import same_answers
from megooflags_api.main import create_app
from megooflags_api.models.cluster import NodeToken
from megooflags_api.services.cluster.snapshot_format import SignedSnapshot
from megooflags_api.services.worker import client as worker_client
from megooflags_api.settings import Settings

STREAM = "/sdk/v1/stream"
ENVIRONMENTS = "/api/projects/demo/environments"


async def ready_worker(cluster: Cluster, name: str = "worker-1") -> tuple[Worker, str]:
    """A synced worker and a production server key."""
    key = (await create_sdk_key(cluster.admin, "server"))["key"]
    worker = await cluster.add_worker(name)
    await cluster.wait_ready(worker)
    return worker, key


async def readiness(worker: Worker, status: str, within: float = 5.0) -> None:
    async def matches() -> bool:
        r = await worker.sdk.get("/readyz")
        return r.json() == {"status": status}

    await eventually(matches, within, f"{worker.name} {status}")


async def stream_events(r: httpx.Response, within: float) -> list[str]:
    """The event names until the stream ends; fails if it is still open after `within` s."""
    async with asyncio.timeout(within):
        return [line[6:].strip() async for line in r.aiter_lines() if line.startswith("event:")]


async def test_revoke_stops_serving_and_survives_restart(cluster: Cluster) -> None:
    worker, key = await ready_worker(cluster)
    headers = bearer(key)

    async with worker.sdk.stream("GET", STREAM, headers=headers) as r:
        assert r.status_code == 200
        r2 = await cluster.admin.post(f"/api/nodes/{worker.node_id}/revoke")
        assert r2.status_code == 200, r2.text
        events = await stream_events(r, 5)
    assert events == ["put", "revoked"]

    await readiness(worker, "revoked")
    assert_error(await worker.sdk.get(RULESET, headers=headers), 503, "revoked")
    assert (await cluster.node(worker.node_id))["status"] == "revoked"

    await worker.restart()
    r = await worker.sdk.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "revoked"}
    await asyncio.sleep(0.5)  # the restarted client is refused again, not let back in
    assert (await worker.sdk.get("/readyz")).json() == {"status": "revoked"}
    assert_error(await worker.sdk.get(RULESET, headers=headers), 503, "revoked")


async def expire_old_tokens(cluster: Cluster, node_id: str) -> None:
    """End the rotation overlap now, then re-check the open worker streams."""
    async with cluster.cp_app.state.sessionmaker() as session:
        await session.execute(
            update(NodeToken)
            .where(NodeToken.node_id == uuid.UUID(node_id), NodeToken.expires_at.is_not(None))
            .values(expires_at=func.now() - func.make_interval(0, 0, 0, 0, 0, 0, 1))
        )
        await session.commit()
    await cluster.cp_app.state.node_hub.recheck_all()


async def test_rotate_overlap_then_new_token(cluster: Cluster) -> None:
    worker, key = await ready_worker(cluster)
    twin = await cluster.add_worker("twin", token=worker.token)  # stays on the old token
    await cluster.wait_ready(twin)

    r = await cluster.admin.post(
        f"/api/nodes/{worker.node_id}/rotate", json={"overlap_seconds": 3600}
    )
    assert r.status_code == 201, r.text
    new_token = r.json()["token"]
    assert new_token != worker.token
    await asyncio.sleep(0.5)  # a few heartbeats with the old token
    for w in (worker, twin):
        assert (await w.sdk.get("/readyz")).status_code == 200
        assert (await w.sdk.get(RULESET, headers=bearer(key))).status_code == 200

    worker.token = new_token
    worker.settings = worker_settings(cluster.cp_url, new_token, worker.data_dir)
    await worker.restart()
    await cluster.wait_ready(worker)
    await cluster.wait_status(worker.node_id, "healthy")

    await expire_old_tokens(cluster, worker.node_id)
    await readiness(twin, "revoked")
    assert_error(await twin.sdk.get(RULESET, headers=bearer(key)), 503, "revoked")
    assert (await worker.sdk.get("/readyz")).status_code == 200
    assert (await worker.sdk.get(RULESET, headers=bearer(key))).status_code == 200
    assert (await cluster.node(worker.node_id))["status"] == "healthy"


async def test_drain_then_resume(cluster: Cluster) -> None:
    worker, key = await ready_worker(cluster)
    headers = bearer(key)

    async with worker.sdk.stream("GET", STREAM, headers=headers) as r:
        assert r.status_code == 200
        r2 = await cluster.admin.post(f"/api/nodes/{worker.node_id}/drain")
        assert r2.status_code == 200, r2.text
        await readiness(worker, "draining")
        assert (await worker.sdk.get(RULESET, headers=headers)).status_code == 200
        events = await stream_events(r, 5)
    assert events == ["put"]  # closed after the grace, without `revoked`
    await cluster.wait_status(worker.node_id, "draining")
    assert (await worker.sdk.get(RULESET, headers=headers)).status_code == 200

    r = await cluster.admin.post(f"/api/nodes/{worker.node_id}/resume")
    assert r.status_code == 200, r.text
    await readiness(worker, "ready")
    await cluster.wait_status(worker.node_id, "healthy")


async def test_incompatible_version_refused_and_shown(cluster: Cluster) -> None:
    key = (await create_sdk_key(cluster.admin, "server"))["key"]
    fresh = await cluster.add_worker("fresh", version="9.0.0")
    node = await cluster.wait_status(fresh.node_id, "incompatible")
    assert node["refused_version"] == "9.0.0"
    r = await fresh.sdk.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "not_ready"}

    worker = await cluster.add_worker("worker-1")
    await cluster.wait_ready(worker)
    before = await worker.sdk.get(RULESET, headers=bearer(key))
    await worker.stop()
    worker.version = "9.0.0"
    await worker.start()
    await cluster.wait_status(worker.node_id, "incompatible")
    assert (await worker.sdk.get("/readyz")).status_code == 200
    after = await worker.sdk.get(RULESET, headers=bearer(key))
    assert after.status_code == 200 and after.headers["etag"] == before.headers["etag"]


async def test_environment_delete_removes_on_worker(cluster: Cluster) -> None:
    r = await cluster.admin.post(ENVIRONMENTS, json={"key": "qa", "name": "QA"})
    assert r.status_code == 201, r.text
    key = (await create_sdk_key(cluster.admin, "server", env="qa"))["key"]
    worker = await cluster.add_worker("worker-1")
    await cluster.wait_ready(worker)
    headers = bearer(key)

    async def served() -> bool:
        return (await worker.sdk.get(RULESET, headers=headers)).status_code == 200

    await eventually(served, 5, "qa served")
    async with worker.sdk.stream("GET", STREAM, headers=headers) as r:
        assert r.status_code == 200
        assert (await cluster.admin.delete(f"{ENVIRONMENTS}/qa")).status_code == 204
        events = await stream_events(r, 5)
    assert events == ["put", "revoked"]
    assert_error(await worker.sdk.get(RULESET, headers=headers), 401, "unauthorized")
    assert (await worker.sdk.get("/readyz")).status_code == 200


async def test_changed_secret_key_refetches_everything(cluster: Cluster) -> None:
    worker, key = await ready_worker(cluster)
    store = worker.app.state.store
    held = store.versions()
    old_key = store.meta.public_key
    apply = store.apply
    applied: list[uuid.UUID] = []

    def counting_apply(snap: SignedSnapshot, **kwargs: Any) -> bool:
        changed: bool = apply(snap, **kwargs)
        if changed:
            applied.append(snap.header.environment_id)
        return changed

    store.apply = counting_apply
    await cluster.restart_control_plane(
        control_plane_settings(secret_key=SecretStr("another-secret-key-0123456789abcdef"))
    )

    async def resigned() -> bool:
        return store.meta.public_key != old_key and set(applied) == set(held)

    await eventually(resigned, 10, "every snapshot fetched again")
    assert sorted(applied) == sorted(held)
    assert store.versions().keys() == held.keys()
    await cluster.wait_ready(worker)
    await same_answers(cluster, worker.url, key)
    await worker.restart()  # the re-signed files verify against the new key
    await cluster.wait_ready(worker, within=0.5)
    await same_answers(cluster, worker.url, key)


async def test_worker_against_standalone_logs_not_control_plane(
    migrated: None, valkey_up: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # create_app reconfigures logging, which drops caplog's root handler: listen on the logger.
    client_logger = logging.getLogger(worker_client.__name__)
    client_logger.addHandler(caplog.handler)
    app = create_app(Settings())
    await reset_database(app)
    try:
        async with running(app) as url:
            settings = worker_settings(url, "mf_node_" + "0" * 32, tmp_path / "worker")
            worker = Worker("worker", "", "", tmp_path / "worker", CuttableTransport(), settings)
            await worker.start()
            try:

                async def logged() -> bool:
                    return any(
                        r.levelno == logging.ERROR and "MEGOOFLAGS_ROLE=control-plane" in r.message
                        for r in caplog.records
                    )

                await eventually(logged, 5, "logged not_control_plane")
                r = await worker.sdk.get("/readyz")
                assert r.status_code == 503 and r.json() == {"status": "not_ready"}
            finally:
                await worker.stop()
    finally:
        client_logger.removeHandler(caplog.handler)
        await app.state.ruleset_cache.aclose()
        await close_app(app)

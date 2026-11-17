import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import httpx
from helpers import bearer, create_sdk_key, next_events
from sqlalchemy import func, select, update
from test_sdk import FLAGS, RULESET

from cluster.cluster_harness import Cluster, Worker, eventually
from megooflags_api.models.cluster import Node, NodeEventBatch
from megooflags_api.models.sdk import EvaluationCount
from megooflags_api.services.cluster.snapshot_format import SignedSnapshot
from megooflags_api.services.scheduler import now_utc
from megooflags_api.services.worker.client import EVENTS_PATH

EVALUATE = "/sdk/v1/evaluate"
EVENTS = "/sdk/v1/events"
STREAM = "/sdk/v1/stream"
CONTEXT = {"targetingKey": "u1", "plan": "pro"}


async def ready_worker(cluster: Cluster) -> tuple[Worker, str]:
    """A synced worker and a production server key."""
    key = (await create_sdk_key(cluster.admin, "server"))["key"]
    worker = await cluster.add_worker("worker-1")
    await cluster.wait_ready(worker)
    return worker, key


async def disconnected(worker: Worker) -> None:
    async def lost() -> bool:
        return not worker.app.state.worker_state.connected

    await eventually(lost, 5, f"{worker.name} disconnected")


def counts_of(n: int) -> dict[str, Any]:
    """An events body counting `n` evaluations of color = blue in this hour."""
    hour = now_utc().replace(minute=0, second=0, microsecond=0).isoformat()
    return {"counts": [{"flag_key": "color", "variant": "blue", "hour": hour, "count": n}]}


async def stored_counts(cluster: Cluster) -> dict[tuple[str, str], int]:
    async with cluster.cp_app.state.sessionmaker() as session:
        rows = await session.execute(
            select(EvaluationCount.flag_key, EvaluationCount.variant, EvaluationCount.count)
        )
        return {(flag_key, variant): count for flag_key, variant, count in rows}


async def forwarded(worker: Worker) -> None:
    """Wait until the worker's buffer is empty: every batch was accepted."""

    async def empty() -> bool:
        return worker.app.state.event_buffer.pending_events == 0

    await eventually(empty, 5, f"{worker.name} forwarded its events")


async def test_link_down_keeps_serving(cluster: Cluster) -> None:
    worker, key = await ready_worker(cluster)
    headers = bearer(key)

    worker.link.cut()
    await disconnected(worker)

    r = await worker.sdk.get(RULESET, headers=headers)
    assert r.status_code == 200 and "color" in r.json()["flags"]
    r = await worker.sdk.post(EVALUATE, json={"context": CONTEXT}, headers=headers)
    assert r.status_code == 200, r.text
    assert {res["key"]: res["value"] for res in r.json()["results"]}["color"] == "blue"
    async with worker.sdk.stream("GET", STREAM, headers=headers) as r:
        assert r.status_code == 200
        (first,) = await next_events(r.aiter_lines(), 1, 2)
        assert first.event == "put"
    metrics = (await worker.sdk.get("/metrics")).text.splitlines()
    assert "megooflags_worker_control_plane_connected 0.0" in metrics
    r = await worker.sdk.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready"}


async def test_restart_from_disk_with_link_down(cluster: Cluster) -> None:
    worker, key = await ready_worker(cluster)
    before = await worker.sdk.get(RULESET, headers=bearer(key))
    assert before.status_code == 200

    worker.link.cut()
    await worker.restart()

    assert (await worker.sdk.get("/readyz")).status_code == 200
    after = await worker.sdk.get(RULESET, headers=bearer(key))
    assert after.status_code == 200
    assert after.headers["etag"] == before.headers["etag"]
    assert after.json() == before.json()


async def test_resync_on_reconnect_sends_only_changes(cluster: Cluster) -> None:
    worker, key = await ready_worker(cluster)
    store = worker.app.state.store
    apply = store.apply
    applied: list[SignedSnapshot] = []

    def counting_apply(snap: SignedSnapshot, **kwargs: Any) -> bool:
        changed: bool = apply(snap, **kwargs)
        if changed:
            applied.append(snap)
        return changed

    store.apply = counting_apply
    worker.link.cut()
    await disconnected(worker)
    r = await cluster.admin.post(f"{FLAGS}/color/environments/production/kill")
    assert r.status_code == 200, r.text
    worker.link.restore()

    async def caught_up() -> bool:
        r = await cluster.admin.get("/api/nodes")
        current: int = r.json()["snapshot_version"]
        return bool(applied) and store.max_version() == current

    await eventually(caught_up, 5, "caught up")
    r = await worker.sdk.get(RULESET, headers=bearer(key))
    assert r.json()["flags"]["color"]["enabled"] is False
    assert len(applied) == 1


async def test_buffered_events_stored_exactly_once(cluster: Cluster) -> None:
    worker, key = await ready_worker(cluster)
    headers = bearer(key)

    worker.link.cut()
    await disconnected(worker)
    r = await worker.sdk.post(EVENTS, json=counts_of(3), headers=headers)
    assert r.status_code == 202, r.text
    for _ in range(2):
        r = await worker.sdk.post(EVALUATE, json={"context": CONTEXT}, headers=headers)
        assert r.status_code == 200, r.text
    assert await stored_counts(cluster) == {}
    worker.link.restore()

    await forwarded(worker)
    expected = {("color", "blue"): 3 + 2, ("new-ui", "false"): 2}
    assert await stored_counts(cluster) == expected
    await worker.app.state.event_buffer.forward(worker.app.state.worker_client.send_events)
    assert await stored_counts(cluster) == expected


def drop_first_response(link: httpx.AsyncBaseTransport, path: str) -> list[str]:
    """Lose the response of the first request to `path` after the server has handled it."""
    handle: Callable[[httpx.Request], Awaitable[httpx.Response]] = link.handle_async_request
    dropped: list[str] = []

    async def lossy(request: httpx.Request) -> httpx.Response:
        response = await handle(request)
        if request.url.path == path and not dropped:
            await response.aread()
            await response.aclose()
            dropped.append(request.content.decode())
            raise httpx.ReadError("the response was lost", request=request)
        return response

    link.handle_async_request = lossy  # type: ignore[method-assign]
    return dropped


async def test_ambiguous_forward_stored_once(cluster: Cluster) -> None:
    worker, key = await ready_worker(cluster)
    dropped = drop_first_response(worker.link, EVENTS_PATH)

    r = await worker.sdk.post(EVENTS, json=counts_of(3), headers=bearer(key))
    assert r.status_code == 202, r.text
    await forwarded(worker)

    assert len(dropped) == 1
    assert await stored_counts(cluster) == {("color", "blue"): 3}
    async with cluster.cp_app.state.sessionmaker() as session:
        batches = await session.scalar(select(func.count()).select_from(NodeEventBatch))
    assert batches == 1


async def test_stale_status_when_heartbeats_stop(cluster: Cluster) -> None:
    worker, _ = await ready_worker(cluster)
    await cluster.wait_status(worker.node_id, "healthy")

    worker.link.cut()
    await disconnected(worker)  # a heartbeat in flight at the cut has landed by now
    async with cluster.cp_app.state.sessionmaker() as session:
        await session.execute(
            update(Node)
            .where(Node.id == uuid.UUID(worker.node_id))
            .values(last_heartbeat_at=Node.last_heartbeat_at - timedelta(seconds=31))
        )
        await session.commit()

    assert (await cluster.node(worker.node_id))["status"] == "stale"

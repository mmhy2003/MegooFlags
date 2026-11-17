import json
import time
from typing import Any

import httpx
from helpers import assert_error, bearer, create_sdk_key, next_events
from test_sdk import FLAGS, RULESET

from cluster.cluster_harness import Cluster, eventually

EVALUATE = "/sdk/v1/evaluate"
STREAM = "/sdk/v1/stream"
OFREP_BULK = "/ofrep/v1/evaluate/flags"
CONTEXT = {"targetingKey": "u1", "plan": "pro"}


async def same_answers(cluster: Cluster, worker_url: str, key: str) -> None:
    """Every SDK answer for `key` on the worker matches the control plane's."""

    async def answers(url: str) -> list[tuple[int, str | None, Any]]:
        async with httpx.AsyncClient(base_url=url, timeout=10, headers=bearer(key)) as c:
            responses = [
                await c.post(EVALUATE, json={"context": CONTEXT}),
                await c.post(f"{OFREP_BULK}/color", json={"context": CONTEXT}),
                await c.post(OFREP_BULK, json={"context": CONTEXT}),
            ]
            if key.startswith("mf_server_"):
                responses.append(await c.get(RULESET))
        return [(r.status_code, r.headers.get("etag"), r.json()) for r in responses]

    expected = await answers(cluster.cp_url)
    assert all(status == 200 for status, _, _ in expected), expected
    assert await answers(worker_url) == expected


async def test_first_sync_makes_worker_ready(cluster: Cluster) -> None:
    key = (await create_sdk_key(cluster.admin, "server"))["key"]
    ghost = await cluster.add_worker("ghost", token="mf_node_" + "0" * 32)

    r = await ghost.sdk.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "not_ready"}
    assert_error(await ghost.sdk.get(RULESET, headers=bearer(key)), 503, "not_ready")

    worker = await cluster.add_worker("worker-1")
    await cluster.wait_ready(worker)
    await cluster.wait_status(worker.node_id, "healthy")

    async def current() -> dict[str, Any] | None:
        r = await cluster.admin.get("/api/nodes")
        [node] = [n for n in r.json()["nodes"] if n["id"] == worker.node_id]
        in_step = node["snapshot_version"] == r.json()["snapshot_version"]
        return node if in_step and not node["syncing"] else None

    node = await eventually(current, 5, "on the current snapshot")
    assert node["version"] is not None
    assert (await ghost.sdk.get("/readyz")).status_code == 503


async def test_change_reaches_worker_sdk_stream_under_5_s(cluster: Cluster) -> None:
    key = (await create_sdk_key(cluster.admin, "server"))["key"]
    worker = await cluster.add_worker("worker-1")
    await cluster.wait_ready(worker)

    async with worker.sdk.stream("GET", STREAM, headers=bearer(key)) as r:
        assert r.status_code == 200
        lines = r.aiter_lines()
        (first,) = await next_events(lines, 1, 2)
        assert json.loads(first.data)["flags"]["color"]["enabled"] is True

        started = time.monotonic()
        r2 = await cluster.admin.post(f"{FLAGS}/color/environments/production/kill")
        assert r2.status_code == 200, r2.text
        (put,) = await next_events(lines, 1, 5)
        while put.comment:  # a `: ping`
            (put,) = await next_events(lines, 1, 5 - (time.monotonic() - started))
        assert time.monotonic() - started < 5

    assert put.event == "put"
    assert json.loads(put.data)["flags"]["color"]["enabled"] is False


async def test_answers_equal_control_plane(cluster: Cluster) -> None:
    server = (await create_sdk_key(cluster.admin, "server"))["key"]
    client = (await create_sdk_key(cluster.admin, "client"))["key"]
    worker = await cluster.add_worker("worker-1")
    await cluster.wait_ready(worker)

    for key in (server, client):
        await same_answers(cluster, worker.url, key)


async def test_revoked_sdk_key_refused_on_worker(cluster: Cluster) -> None:
    created = await create_sdk_key(cluster.admin, "server")
    worker = await cluster.add_worker("worker-1")
    await cluster.wait_ready(worker)
    headers = bearer(created["key"])
    assert (await worker.sdk.get(RULESET, headers=headers)).status_code == 200

    keys = "/api/projects/demo/environments/production/sdk-keys"
    assert (await cluster.admin.delete(f"{keys}/{created['id']}")).status_code == 204

    async def refused() -> httpx.Response | None:
        r = await worker.sdk.get(RULESET, headers=headers)
        return r if r.status_code != 200 else None

    assert_error(await eventually(refused, 5, "refused"), 403, "forbidden")
    async with httpx.AsyncClient(base_url=cluster.cp_url, timeout=10) as cp:
        assert_error(await cp.get(RULESET, headers=headers), 403, "forbidden")


async def test_new_sdk_key_works_on_worker(cluster: Cluster) -> None:
    worker = await cluster.add_worker("worker-1")
    await cluster.wait_ready(worker)
    key = (await create_sdk_key(cluster.admin, "server"))["key"]

    async def served() -> httpx.Response | None:
        r = await worker.sdk.get(RULESET, headers=bearer(key))
        return r if r.status_code == 200 else None

    await eventually(served, 5, "served")
    await same_answers(cluster, worker.url, key)

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import assert_error, create_project
from sqlalchemy import select, text
from test_sdk import environment

from megooflags_api import __version__
from megooflags_api.models import NodeEventBatch
from megooflags_api.models.sdk import EvaluationCount, ExposureEvent

HEARTBEAT = "/api/cluster/v1/heartbeat"
EVENTS = "/api/cluster/v1/events"


@pytest.fixture
async def cp_api(app: FastAPI, api: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    """The admin client, on an app running as a control plane."""
    app.state.settings = app.state.settings.model_copy(update={"role": "control-plane"})
    yield api


def headers(token: str, version: str | None = __version__) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if version is not None:
        h["X-MegooFlags-Version"] = version
    return h


def heartbeat(**extra: Any) -> dict[str, Any]:
    return {
        "version": __version__,
        "snapshot_version": 3,
        "snapshot_age_seconds": 60,
        "sdk_streams": 2,
        "buffered_events": 5,
        "ready": True,
        **extra,
    }


def batch(environment_id: uuid.UUID, batch_id: uuid.UUID | None = None, **extra: Any) -> Any:
    return {
        "batch_id": str(batch_id or uuid.uuid4()),
        "environment_id": str(environment_id),
        "exposures": [
            {
                "flag_key": "color",
                "variant": "red",
                "reason": "TARGETING_MATCH",
                "timestamp": "2026-11-10T10:17:00Z",
                "context": {"key": "u1"},
            }
        ],
        "counts": [
            {"flag_key": "color", "variant": "red", "hour": "2026-11-10T10:00:00Z", "count": 4},
            {"flag_key": "color", "variant": "red", "hour": "2026-11-10T10:30:00Z", "count": 1},
        ],
        **extra,
    }


async def create_node(api: httpx.AsyncClient, name: str = "worker-1") -> dict[str, Any]:
    r = await api.post("/api/nodes", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def listed(api: httpx.AsyncClient, node_id: str) -> dict[str, Any]:
    r = await api.get("/api/nodes")
    assert r.status_code == 200, r.text
    [node] = [n for n in r.json()["nodes"] if n["id"] == node_id]
    return node  # type: ignore[no-any-return]


async def exposures(app: FastAPI) -> list[ExposureEvent]:
    async with app.state.sessionmaker() as session:
        return list(await session.scalars(select(ExposureEvent)))


async def counts(app: FastAPI) -> list[tuple[str, str, datetime, int]]:
    async with app.state.sessionmaker() as session:
        rows = await session.execute(
            select(
                EvaluationCount.flag_key,
                EvaluationCount.variant,
                EvaluationCount.hour,
                EvaluationCount.count,
            )
        )
        return [tuple(row) for row in rows]  # type: ignore[misc]


async def test_standalone_answers_409_not_control_plane(api: httpx.AsyncClient) -> None:
    r = await api.post(HEARTBEAT, json=heartbeat(), headers=headers("mf_node_" + "x" * 32))
    body = assert_error(r, 409, "not_control_plane")
    assert "MEGOOFLAGS_ROLE=control-plane on it" in body["message"]


async def test_unknown_token_401(cp_api: httpx.AsyncClient) -> None:
    await create_node(cp_api)
    r = await cp_api.post(HEARTBEAT, json=heartbeat(), headers=headers("mf_node_" + "x" * 32))
    assert_error(r, 401, "unauthorized")
    r = await cp_api.post(
        HEARTBEAT, json=heartbeat(), headers={"Authorization": "", "X-MegooFlags-Version": "0"}
    )
    assert_error(r, 401, "unauthorized")


async def test_revoked_403_node_revoked(cp_api: httpx.AsyncClient) -> None:
    node = await create_node(cp_api)
    r = await cp_api.post(f"/api/nodes/{node['id']}/revoke")
    assert r.status_code == 200, r.text

    r = await cp_api.post(HEARTBEAT, json=heartbeat(), headers=headers(node["token"]))

    assert_error(r, 403, "node_revoked")


async def test_expired_token_403(app: FastAPI, cp_api: httpx.AsyncClient) -> None:
    node = await create_node(cp_api)
    r = await cp_api.post(f"/api/nodes/{node['id']}/rotate", json={"overlap_seconds": 1})
    assert r.status_code == 201, r.text
    new_token = r.json()["token"]
    async with app.state.sessionmaker() as session:
        await session.execute(
            text(
                "UPDATE node_token SET expires_at = now() - interval '1 minute' "
                "WHERE expires_at IS NOT NULL"
            )
        )
        await session.commit()

    r = await cp_api.post(HEARTBEAT, json=heartbeat(), headers=headers(node["token"]))
    assert_error(r, 403, "node_revoked")
    r = await cp_api.post(HEARTBEAT, json=heartbeat(), headers=headers(new_token))
    assert r.status_code == 200, r.text


async def test_incompatible_409_records_refusal(cp_api: httpx.AsyncClient) -> None:
    node = await create_node(cp_api)

    r = await cp_api.post(
        HEARTBEAT, json=heartbeat(version="9.0.0"), headers=headers(node["token"], "9.0.0")
    )

    body = assert_error(r, 409, "incompatible_version")
    assert "9.0.0" in body["message"] and __version__ in body["message"]
    shown = await listed(cp_api, node["id"])
    assert shown["status"] == "incompatible"
    assert shown["refused_version"] == "9.0.0"

    # A compatible heartbeat clears the refusal.
    r = await cp_api.post(HEARTBEAT, json=heartbeat(), headers=headers(node["token"]))
    assert r.status_code == 200, r.text
    shown = await listed(cp_api, node["id"])
    assert shown["status"] == "healthy"
    assert shown["refused_version"] is None


async def test_missing_version_header_409(cp_api: httpx.AsyncClient) -> None:
    node = await create_node(cp_api)

    r = await cp_api.post(HEARTBEAT, json=heartbeat(), headers=headers(node["token"], None))

    body = assert_error(r, 409, "incompatible_version")
    assert "worker unknown" in body["message"]
    assert (await listed(cp_api, node["id"]))["refused_version"] == "unknown"


async def test_heartbeat_updates_node_and_returns_state(cp_api: httpx.AsyncClient) -> None:
    node = await create_node(cp_api)

    r = await cp_api.post(HEARTBEAT, json=heartbeat(), headers=headers(node["token"]))

    assert r.status_code == 200, r.text
    assert r.json() == {"state": "active"}
    shown = await listed(cp_api, node["id"])
    assert shown["status"] == "healthy"
    assert shown["version"] == __version__
    assert shown["snapshot_version"] == 3
    assert shown["sdk_streams"] == 2
    assert shown["buffered_events"] == 5
    assert shown["address"] == "127.0.0.1"
    last = datetime.fromisoformat(shown["last_heartbeat_at"])
    assert datetime.fromisoformat(shown["synced_at"]) == last - timedelta(seconds=60)

    r = await cp_api.post(f"/api/nodes/{node['id']}/drain")
    assert r.status_code == 200, r.text
    r = await cp_api.post(
        HEARTBEAT, json=heartbeat(snapshot_age_seconds=None), headers=headers(node["token"])
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"state": "draining"}
    # Without an age, the last synced time is kept.
    assert (await listed(cp_api, node["id"]))["synced_at"] == shown["synced_at"]


@pytest.mark.parametrize("age", [1e9 + 1, 1e300])
async def test_heartbeat_rejects_huge_snapshot_age(cp_api: httpx.AsyncClient, age: float) -> None:
    node = await create_node(cp_api)

    r = await cp_api.post(
        HEARTBEAT, json=heartbeat(snapshot_age_seconds=age), headers=headers(node["token"])
    )

    assert r.status_code == 422, r.text


async def test_events_stored_once(app: FastAPI, cp_api: httpx.AsyncClient) -> None:
    node = await create_node(cp_api)
    await create_project(cp_api)
    env = await environment(app)
    body = batch(env.id)

    for _ in range(2):
        r = await cp_api.post(EVENTS, json=body, headers=headers(node["token"]))
        assert r.status_code == 202, r.text
        assert r.content == b""

    assert [(e.environment_id, e.flag_key, e.variant) for e in await exposures(app)] == [
        (env.id, "color", "red")
    ]
    # Both counts fall in the 10:00 hour: summed per key, added once.
    assert await counts(app) == [("color", "red", datetime(2026, 11, 10, 10, tzinfo=UTC), 5)]
    async with app.state.sessionmaker() as session:
        stored = (await session.scalars(select(NodeEventBatch))).all()
    assert [(b.batch_id, str(b.node_id)) for b in stored] == [
        (uuid.UUID(body["batch_id"]), node["id"])
    ]


async def test_events_for_deleted_environment_accepted(
    app: FastAPI, cp_api: httpx.AsyncClient
) -> None:
    node = await create_node(cp_api)
    await create_project(cp_api)
    env = await environment(app, "development")
    r = await cp_api.delete("/api/projects/demo/environments/development")
    assert r.status_code == 204, r.text

    r = await cp_api.post(EVENTS, json=batch(env.id), headers=headers(node["token"]))

    assert r.status_code == 202, r.text
    assert await exposures(app) == []
    assert await counts(app) == []
    async with app.state.sessionmaker() as session:
        assert len((await session.scalars(select(NodeEventBatch))).all()) == 1


async def test_events_context_dropped_unless_persist(
    app: FastAPI, cp_api: httpx.AsyncClient
) -> None:
    node = await create_node(cp_api)
    await create_project(cp_api)
    env = await environment(app)

    r = await cp_api.post(EVENTS, json=batch(env.id), headers=headers(node["token"]))
    assert r.status_code == 202, r.text
    app.state.settings = app.state.settings.model_copy(update={"persist_context": True})
    r = await cp_api.post(EVENTS, json=batch(env.id), headers=headers(node["token"]))
    assert r.status_code == 202, r.text

    assert sorted((e.context is None) for e in await exposures(app)) == [False, True]
    assert [e.context for e in await exposures(app) if e.context] == [{"key": "u1"}]

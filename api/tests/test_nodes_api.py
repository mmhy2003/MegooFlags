import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import assert_error, audit_entries, seed_user, sign_in

from megooflags_api import __version__

NODE_FIELDS = {
    "id",
    "name",
    "status",
    "syncing",
    "created_at",
    "last_heartbeat_at",
    "synced_at",
    "version",
    "snapshot_version",
    "sdk_streams",
    "buffered_events",
    "address",
    "refused_version",
    "token_prefix",
}


@pytest.fixture
async def cp_api(app: FastAPI, api: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    """The admin client, on an app running as a control plane."""
    app.state.settings = app.state.settings.model_copy(update={"role": "control-plane"})
    yield api


async def create_node(api: httpx.AsyncClient, name: str = "worker-1") -> dict[str, Any]:
    r = await api.post("/api/nodes", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_create_list_rotate_revoke_drain_resume_delete(cp_api: httpx.AsyncClient) -> None:
    created = await create_node(cp_api)
    assert set(created) == NODE_FIELDS | {"token"}
    assert created["name"] == "worker-1"
    assert created["status"] == "pending"
    assert created["syncing"] is False
    assert created["token"].startswith("mf_node_")
    assert created["token_prefix"] == created["token"][:12]
    node = created["id"]

    r = await cp_api.get("/api/nodes")
    assert r.status_code == 200, r.text
    [listed] = r.json()["nodes"]
    assert set(listed) == NODE_FIELDS
    assert listed["id"] == node and listed["status"] == "pending"

    r = await cp_api.post(f"/api/nodes/{node}/drain")
    assert r.status_code == 200, r.text
    assert set(r.json()) == NODE_FIELDS
    assert r.json()["status"] == "draining"

    r = await cp_api.post(f"/api/nodes/{node}/resume")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"

    r = await cp_api.post(f"/api/nodes/{node}/rotate", json={"overlap_seconds": 0})
    assert r.status_code == 201, r.text
    rotated = r.json()
    assert set(rotated) == NODE_FIELDS | {"token"}
    assert rotated["token"] != created["token"]
    assert rotated["token_prefix"] == rotated["token"][:12]
    assert rotated["status"] == "pending"

    r = await cp_api.post(f"/api/nodes/{node}/revoke")
    assert r.status_code == 200, r.text
    assert set(r.json()) == NODE_FIELDS
    assert r.json()["status"] == "revoked"
    assert r.json()["token_prefix"] is None

    r = await cp_api.post(f"/api/nodes/{node}/drain")
    assert_error(r, 409, "invalid_state")

    r = await cp_api.delete(f"/api/nodes/{node}")
    assert r.status_code == 204, r.text
    r = await cp_api.get("/api/nodes")
    assert r.json()["nodes"] == []
    r = await cp_api.post(f"/api/nodes/{node}/revoke")
    assert_error(r, 404, "not_found")


async def test_create_duplicate_and_invalid_name(cp_api: httpx.AsyncClient) -> None:
    await create_node(cp_api)

    assert_error(await cp_api.post("/api/nodes", json={"name": "worker-1"}), 409, "key_conflict")
    r = await cp_api.post("/api/nodes", json={"name": "-bad name"})
    assert_error(r, 422, "validation_error")


async def test_list_has_versions(cp_api: httpx.AsyncClient) -> None:
    r = await cp_api.get("/api/nodes")

    assert r.status_code == 200, r.text
    assert r.json() == {"version": __version__, "snapshot_version": 0, "nodes": []}


async def test_standalone_409_not_control_plane(api: httpx.AsyncClient) -> None:
    node = uuid.uuid4()
    calls: list[tuple[str, str, Any]] = [
        ("GET", "/api/nodes", None),
        ("POST", "/api/nodes", {"name": "worker-1"}),
        ("POST", f"/api/nodes/{node}/rotate", {"overlap_seconds": 0}),
        ("POST", f"/api/nodes/{node}/revoke", None),
        ("POST", f"/api/nodes/{node}/drain", None),
        ("POST", f"/api/nodes/{node}/resume", None),
        ("DELETE", f"/api/nodes/{node}", None),
    ]
    for method, path, json_body in calls:
        r = await api.request(method, path, json=json_body)
        body = assert_error(r, 409, "not_control_plane")
        assert "MEGOOFLAGS_ROLE=control-plane" in body["message"]


async def test_requires_org_manage(app: FastAPI, anon: httpx.AsyncClient) -> None:
    app.state.settings = app.state.settings.model_copy(update={"role": "control-plane"})

    assert_error(await anon.get("/api/nodes"), 401, "unauthorized")

    await seed_user(app, "viewer@example.com", role="viewer")
    r = await sign_in(anon, "viewer@example.com")
    assert r.status_code == 200, r.text
    assert_error(await anon.get("/api/nodes"), 403, "forbidden")
    assert_error(await anon.post("/api/nodes", json={"name": "worker-1"}), 403, "forbidden")


async def test_delete_unrevoked_409(cp_api: httpx.AsyncClient) -> None:
    node = (await create_node(cp_api))["id"]

    assert_error(await cp_api.delete(f"/api/nodes/{node}"), 409, "invalid_state")


async def run_all_actions(api: httpx.AsyncClient) -> tuple[str, list[str]]:
    """Create a node and run every action on it; returns its id and every token issued."""
    created = await create_node(api)
    node: str = created["id"]
    tokens = [created["token"]]
    for path, body in [
        ("drain", None),
        ("resume", None),
        ("rotate", {"overlap_seconds": 60}),
        ("revoke", None),
    ]:
        r = await api.post(f"/api/nodes/{node}/{path}", json=body)
        assert r.status_code < 300, r.text
        if "token" in r.json():
            tokens.append(r.json()["token"])
    r = await api.delete(f"/api/nodes/{node}")
    assert r.status_code == 204, r.text
    return node, tokens


async def test_audit_entries_never_hold_the_token(app: FastAPI, cp_api: httpx.AsyncClient) -> None:
    node, tokens = await run_all_actions(cp_api)

    entries = [e for e in await audit_entries(app) if e["resource_type"] == "node"]
    assert [e["action"] for e in entries] == [
        "node.create",
        "node.drain",
        "node.resume",
        "node.rotate",
        "node.revoke",
        "node.delete",
    ]
    for entry in entries:
        assert entry["resource_key"] == node
        # token_prefix (the first 12 characters) is kept; the token never is.
        text = json.dumps(entry, default=str)
        assert len(tokens) == 2 and all(token not in text for token in tokens)
    assert set(entries[0]["after"]) == NODE_FIELDS
    assert entries[-1]["before"]["status"] == "revoked"


async def test_node_actions_publish_node_message(app: FastAPI, cp_api: httpx.AsyncClient) -> None:
    seen: list[uuid.UUID] = []
    app.state.change_bus.on_node.append(seen.append)

    node, _ = await run_all_actions(cp_api)
    await cp_api.get("/api/nodes")

    assert seen == [uuid.UUID(node)] * 5

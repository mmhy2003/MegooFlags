import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    assert_error,
    audit_entries,
    bearer,
    create_project,
    seed_service_token,
    seed_user,
)

from megooflags_api.services.api_tokens import create_personal_token
from megooflags_api.services.users import get_user


@asynccontextmanager
async def client_with(app: FastAPI, token: str) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=bearer(token)
    ) as client:
        yield client


@pytest.fixture
async def admin(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with client_with(app, await seed_service_token(app)) as client:
        yield client


async def create_token(client: httpx.AsyncClient, name: str = "ci", **body: Any) -> dict[str, Any]:
    r = await client.post("/api/tokens", json={"name": name, **body})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


EDITOR_ON_DEMO = {"role": "editor", "scope": "project", "project": "demo"}


async def test_create_with_bindings_returns_the_secret_once(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    await create_project(admin)
    created = await create_token(admin, bindings=[EDITOR_ON_DEMO])

    secret = created["secret"]
    assert secret.startswith("mf_api_")
    token = created["token"]
    assert token["kind"] == "service"
    assert token["name"] == "ci"
    assert token["prefix"] == secret[:14]
    assert token["revoked_at"] is None
    assert token["expires_at"] is None
    assert token["rotated_from_id"] is None
    assert [
        (b["subject_kind"], b["subject_id"], b["role"], b["scope"], b["project"], b["env"])
        for b in token["bindings"]
    ] == [("service_token", token["id"], "editor", "project", "demo", None)]

    async with client_with(app, secret) as client:
        r = await client.get("/api/roles")
        assert r.status_code == 200, r.text


async def test_create_without_bindings(admin: httpx.AsyncClient) -> None:
    created = await create_token(
        admin, expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()
    )
    assert created["token"]["bindings"] == []
    assert created["token"]["expires_at"] is not None


async def test_create_with_an_unknown_project_is_404(admin: httpx.AsyncClient) -> None:
    r = await admin.post("/api/tokens", json={"name": "ci", "bindings": [EDITOR_ON_DEMO]})
    assert_error(r, 404, "not_found")
    r = await admin.get("/api/tokens")
    assert [t["name"] for t in r.json()["items"]] == ["tests"]


async def test_list_shows_service_tokens_without_secrets(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    owner_id = await seed_user(app)
    async with app.state.sessionmaker() as session:
        owner = await get_user(session, owner_id)
        await create_personal_token(session, owner, "mine", ["project.read"], None, None, None)
        await session.commit()
    created = await create_token(admin)

    r = await admin.get("/api/tokens")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next_cursor"] is None
    assert sorted(t["name"] for t in body["items"]) == ["ci", "tests"]
    assert {t["kind"] for t in body["items"]} == {"service"}
    assert created["secret"] not in r.text
    assert all("secret" not in t and "secret_hash" not in t for t in body["items"])
    seeded = next(t for t in body["items"] if t["name"] == "tests")
    assert [(b["role"], b["scope"]) for b in seeded["bindings"]] == [("admin", "org")]

    r = await admin.get(f"/api/tokens/{created['token']['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == created["token"]


async def test_list_pages(admin: httpx.AsyncClient) -> None:
    for name in ("a", "b", "c"):
        await create_token(admin, name)
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        r = await admin.get("/api/tokens", params=params)
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) <= 2
        seen += [t["name"] for t in r.json()["items"]]
        cursor = r.json()["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == ["a", "b", "c", "tests"]


async def test_a_personal_token_or_unknown_id_is_404(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    owner_id = await seed_user(app)
    async with app.state.sessionmaker() as session:
        owner = await get_user(session, owner_id)
        row, _ = await create_personal_token(session, owner, "mine", [], None, None, None)
        await session.commit()
    assert_error(await admin.get(f"/api/tokens/{row.id}"), 404, "not_found")
    assert_error(await admin.delete(f"/api/tokens/{row.id}"), 404, "not_found")


async def test_revoke(app: FastAPI, admin: httpx.AsyncClient) -> None:
    created = await create_token(admin, bindings=[{"role": "viewer", "scope": "org"}])
    token_id = created["token"]["id"]

    r = await admin.delete(f"/api/tokens/{token_id}")
    assert r.status_code == 204, r.text
    revoked_at = (await admin.get(f"/api/tokens/{token_id}")).json()["revoked_at"]
    assert revoked_at is not None

    async with client_with(app, created["secret"]) as client:
        assert_error(await client.get("/api/roles"), 401, "unauthorized")

    r = await admin.delete(f"/api/tokens/{token_id}")
    assert r.status_code == 204, r.text
    assert (await admin.get(f"/api/tokens/{token_id}")).json()["revoked_at"] == revoked_at


async def test_expires_at_in_the_past_is_422(admin: httpx.AsyncClient) -> None:
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    r = await admin.post("/api/tokens", json={"name": "ci", "expires_at": past})
    assert_error(r, 422, "validation_error")


async def test_a_viewer_gets_403(app: FastAPI, admin: httpx.AsyncClient) -> None:
    created = await create_token(admin)
    async with client_with(app, await seed_service_token(app, "viewer", name="v")) as viewer:
        assert_error(await viewer.get("/api/tokens"), 403, "forbidden")
        assert_error(await viewer.get(f"/api/tokens/{created['token']['id']}"), 403, "forbidden")
        assert_error(await viewer.post("/api/tokens", json={"name": "x"}), 403, "forbidden")
        assert_error(await viewer.delete(f"/api/tokens/{created['token']['id']}"), 403, "forbidden")


async def test_create_and_revoke_are_audited_without_the_secret(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    await create_project(admin)
    created = await create_token(admin, bindings=[EDITOR_ON_DEMO])
    token_id = created["token"]["id"]

    [entry] = await audit_entries(app, "token.create")
    assert (entry["actor_label"], entry["resource_type"], entry["resource_key"]) == (
        "tests",
        "token",
        token_id,
    )
    assert entry["after"]["name"] == "ci"
    assert entry["after"]["bindings"][0]["role"] == "editor"
    assert created["secret"] not in json.dumps(entry, default=str)

    await admin.delete(f"/api/tokens/{token_id}")
    [entry] = await audit_entries(app, "token.revoke")
    assert entry["resource_key"] == token_id
    assert entry["before"]["revoked_at"] is None
    assert entry["after"]["revoked_at"] is not None


async def test_last_used_at_is_set_on_first_use(app: FastAPI, admin: httpx.AsyncClient) -> None:
    created = await create_token(admin, bindings=[{"role": "viewer", "scope": "org"}])
    assert created["token"]["last_used_at"] is None
    async with client_with(app, created["secret"]) as client:
        assert (await client.get("/api/roles")).status_code == 200
    r = await admin.get(f"/api/tokens/{created['token']['id']}")
    assert r.json()["last_used_at"] is not None

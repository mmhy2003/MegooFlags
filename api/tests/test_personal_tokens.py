"""Personal API tokens (`/account/tokens`): the owner's rights, capped per request."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    ORIGIN,
    assert_error,
    audit_entries,
    bearer,
    create_flag,
    create_project,
    seed_binding,
    seed_user,
    sign_in,
)
from sqlalchemy import delete, update

from megooflags_api.models import RoleBinding, User
from megooflags_api.models.enums import SubjectKind, UserStatus

EDITOR = "editor@example.com"
TOGGLE = "/api/projects/demo/flags/new-ui/environments/production/toggle"
ON = {"enabled": True}
DEMO_TOKEN = {"name": "ci", "permissions": ["project.read", "flag.toggle"], "project": "demo"}


@asynccontextmanager
async def client(app: FastAPI, headers: dict[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c


@asynccontextmanager
async def signed_in(app: FastAPI, email: str = EDITOR) -> AsyncIterator[httpx.AsyncClient]:
    async with client(app, ORIGIN) as c:
        r = await sign_in(c, email)
        assert r.status_code == 200, r.text
        yield c


@pytest.fixture
async def editor(app: FastAPI, api: httpx.AsyncClient) -> uuid.UUID:
    """An org editor, with projects `demo` (holding flag `new-ui`) and `other`."""
    await create_project(api)
    await create_project(api, "other")
    await create_flag(api)
    return await seed_user(app, EDITOR, role="editor")


async def create_token(app: FastAPI, body: dict[str, Any], email: str = EDITOR) -> httpx.Response:
    async with signed_in(app, email) as c:
        return await c.post("/api/account/tokens", json=body)


async def demo_token(app: FastAPI) -> str:
    r = await create_token(app, DEMO_TOKEN)
    assert r.status_code == 201, r.text
    secret: str = r.json()["secret"]
    return secret


async def test_create_returns_the_token_and_its_secret_once(
    app: FastAPI, editor: uuid.UUID
) -> None:
    r = await create_token(app, DEMO_TOKEN)

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["secret"].startswith("mf_api_")
    token = body["token"]
    assert token["name"] == "ci"
    assert token["prefix"] == body["secret"][:14]
    assert sorted(token["permissions"]) == ["flag.toggle", "project.read"]
    assert (token["project"], token["env"]) == ("demo", None)
    assert token["revoked_at"] is None


async def test_the_token_acts_within_its_permissions_and_scope(
    app: FastAPI, editor: uuid.UUID
) -> None:
    async with client(app, bearer(await demo_token(app))) as c:
        assert (await c.get("/api/projects/demo/flags")).status_code == 200
        assert (await c.post(TOGGLE, json=ON)).status_code == 200
        r = await c.patch("/api/projects/demo/flags/new-ui", json={"name": "New"})
        assert_error(r, 403, "forbidden")
        assert_error(await c.get("/api/projects/other/flags"), 404, "not_found")
        r = await c.post("/api/projects", json={"key": "third", "name": "Third"})
        assert_error(r, 403, "forbidden")


async def test_an_environment_scoped_token(app: FastAPI, editor: uuid.UUID) -> None:
    r = await create_token(app, DEMO_TOKEN | {"env": "production"})
    assert r.status_code == 201, r.text
    assert r.json()["token"]["env"] == "production"

    async with client(app, bearer(r.json()["secret"])) as c:
        assert (await c.post(TOGGLE, json=ON)).status_code == 200
        r = await c.post("/api/projects/demo/flags/new-ui/environments/development/toggle", json=ON)
        assert_error(r, 404, "not_found")


async def test_env_needs_project(app: FastAPI, editor: uuid.UUID) -> None:
    r = await create_token(app, {"name": "ci", "permissions": ["flag.toggle"], "env": "production"})

    assert_error(r, 422, "validation_error")


async def test_permissions_are_required(app: FastAPI, editor: uuid.UUID) -> None:
    r = await create_token(app, {"name": "ci", "permissions": []})

    assert_error(r, 422, "validation_error")


async def test_unknown_project_is_404(app: FastAPI, editor: uuid.UUID) -> None:
    r = await create_token(app, DEMO_TOKEN | {"project": "nope"})

    assert_error(r, 404, "not_found")


async def test_more_than_the_owner_holds_is_refused(app: FastAPI, editor: uuid.UUID) -> None:
    r = await create_token(
        app, {"name": "ci", "permissions": ["project.read", "member.manage", "audit.read"]}
    )

    body = assert_error(r, 422, "exceeds_owner_rights")
    assert body["details"] == {"permissions": ["audit.read", "member.manage"]}
    assert await audit_entries(app, "account.token.create") == []


async def test_removing_the_owners_binding_caps_the_token(app: FastAPI, editor: uuid.UUID) -> None:
    await seed_user(app, "second-admin@example.com")
    secret = await demo_token(app)
    async with app.state.sessionmaker() as session:
        await session.execute(
            delete(RoleBinding).where(
                RoleBinding.subject_kind == SubjectKind.USER, RoleBinding.subject_id == editor
            )
        )
        await session.commit()

    async with client(app, bearer(secret)) as c:
        assert_error(await c.post(TOGGLE, json=ON), 404, "not_found")
        assert_error(await c.get("/api/projects/demo"), 404, "not_found")


async def test_removing_a_permission_from_the_owner_forbids_it(
    app: FastAPI, editor: uuid.UUID
) -> None:
    secret = await demo_token(app)
    async with app.state.sessionmaker() as session:
        await session.execute(
            delete(RoleBinding).where(
                RoleBinding.subject_kind == SubjectKind.USER, RoleBinding.subject_id == editor
            )
        )
        await session.commit()
    await seed_binding(app, SubjectKind.USER, editor, "viewer")

    async with client(app, bearer(secret)) as c:
        assert (await c.get("/api/projects/demo")).status_code == 200
        assert_error(await c.post(TOGGLE, json=ON), 403, "forbidden")


async def test_disabling_the_owner_stops_the_token(app: FastAPI, editor: uuid.UUID) -> None:
    secret = await demo_token(app)
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(User).where(User.id == editor).values(status=UserStatus.DISABLED)
        )
        await session.commit()

    async with client(app, bearer(secret)) as c:
        assert_error(await c.get("/api/projects/demo/flags"), 401, "unauthorized")


async def test_list_shows_only_your_own_tokens(app: FastAPI, editor: uuid.UUID) -> None:
    await seed_user(app, "someone@example.com", role="editor")
    await demo_token(app)
    r = await create_token(
        app, {"name": "theirs", "permissions": ["project.read"]}, "someone@example.com"
    )
    assert r.status_code == 201, r.text

    async with signed_in(app) as c:
        r = await c.get("/api/account/tokens")

    assert r.status_code == 200, r.text
    assert [t["name"] for t in r.json()] == ["ci"]


async def test_revoke_stops_the_token(app: FastAPI, editor: uuid.UUID) -> None:
    r = await create_token(app, DEMO_TOKEN)
    token_id, secret = r.json()["token"]["id"], r.json()["secret"]

    async with signed_in(app) as c:
        assert (await c.delete(f"/api/account/tokens/{token_id}")).status_code == 204
        [listed] = (await c.get("/api/account/tokens")).json()
    assert listed["revoked_at"] is not None
    async with client(app, bearer(secret)) as c:
        assert_error(await c.get("/api/projects/demo/flags"), 401, "unauthorized")


async def test_another_users_token_is_404(app: FastAPI, editor: uuid.UUID) -> None:
    await seed_user(app, "someone@example.com", role="editor")
    r = await create_token(
        app, {"name": "theirs", "permissions": ["project.read"]}, "someone@example.com"
    )
    theirs = r.json()["token"]["id"]

    async with signed_in(app) as c:
        assert_error(await c.delete(f"/api/account/tokens/{theirs}"), 404, "not_found")
        assert_error(await c.delete(f"/api/account/tokens/{uuid.uuid4()}"), 404, "not_found")


async def test_a_token_cannot_manage_personal_tokens(app: FastAPI, editor: uuid.UUID) -> None:
    async with client(app, bearer(await demo_token(app))) as c:
        assert_error(await c.get("/api/account/tokens"), 403, "forbidden")
        r = await c.post("/api/account/tokens", json=DEMO_TOKEN)
        assert_error(r, 403, "forbidden")


async def test_create_and_revoke_are_audited(app: FastAPI, editor: uuid.UUID) -> None:
    secret = await demo_token(app)
    async with client(app, bearer(secret)) as c:
        assert (await c.post(TOGGLE, json=ON)).status_code == 200
    [created] = await audit_entries(app, "account.token.create")
    token_id = created["resource_key"]
    async with signed_in(app) as c:
        assert (await c.delete(f"/api/account/tokens/{token_id}")).status_code == 204

    assert created["actor_kind"] == "user"
    assert created["after"]["name"] == "ci"
    [revoked] = await audit_entries(app, "account.token.revoke")
    assert revoked["resource_key"] == token_id
    assert revoked["after"]["revoked_at"] is not None
    [toggled] = await audit_entries(app, "flag.toggle")
    assert toggled["actor_kind"] == "personal_token"
    assert str(toggled["actor_id"]) == token_id
    assert toggled["actor_label"] == f"{EDITOR} (ci)"


async def test_service_token_list_hides_personal_tokens(
    app: FastAPI, api: httpx.AsyncClient, editor: uuid.UUID
) -> None:
    await demo_token(app)

    r = await api.get("/api/tokens")

    assert r.status_code == 200, r.text
    assert [t["kind"] for t in r.json()["items"]] == ["service"]

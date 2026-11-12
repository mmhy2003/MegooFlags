"""`GET /auth/me/permissions`: what the caller may do org-wide, in a project and per environment."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from helpers import ORIGIN, assert_error, bearer, create_project, seed_user, sign_in
from sqlalchemy import select

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import PUBLIC_ROUTES
from megooflags_api.models import Environment, Project

PERMISSIONS = "/api/auth/me/permissions"
ENVS = [
    {"key": "development", "name": "Development"},
    {"key": "production", "name": "Production"},
]


@pytest.fixture
async def demo(api: httpx.AsyncClient) -> None:
    await create_project(api, "demo", ENVS)
    await create_project(api, "other", ENVS)


async def scope_ids(
    app: FastAPI, project: str, env: str | None
) -> tuple[uuid.UUID, uuid.UUID | None]:
    async with app.state.sessionmaker() as session:
        project_id = await session.scalar(select(Project.id).where(Project.key == project))
        assert project_id is not None
        if env is None:
            return project_id, None
        env_id = await session.scalar(
            select(Environment.id).where(
                Environment.project_id == project_id, Environment.key == env
            )
        )
        assert env_id is not None
        return project_id, env_id


@asynccontextmanager
async def user_client(
    app: FastAPI,
    email: str,
    role: str,
    scope: str = "org",
    project: str | None = None,
    env: str | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """A signed-in session for a new user holding `role` at `scope`."""
    project_id, env_id = (None, None) if project is None else await scope_ids(app, project, env)
    await seed_user(
        app, email, role=role, scope=scope, project_id=project_id, environment_id=env_id
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=ORIGIN) as c:
        r = await sign_in(c, email)
        assert r.status_code == 200, r.text
        yield c


async def get(client: httpx.AsyncClient, project: str | None = None) -> httpx.Response:
    params = {} if project is None else {"project": project}
    return await client.get(PERMISSIONS, params=params)


async def test_without_project_returns_org_only(app: FastAPI, demo: None) -> None:
    async with user_client(app, "admin@example.com", "admin") as c:
        r = await get(c)

    assert r.status_code == 200, r.text
    assert r.json() == {"org": [p.value for p in Permission], "project": None, "environments": {}}


async def test_admin_in_project(app: FastAPI, demo: None) -> None:
    async with user_client(app, "admin@example.com", "admin") as c:
        r = await get(c, "demo")

    assert r.status_code == 200, r.text
    body = r.json()
    assert {"project.manage", "audit.read"} <= set(body["project"])
    assert set(body["environments"]) == {"development", "production"}
    for perms in body["environments"].values():
        assert {"sdk_key.read", "flag.toggle"} <= set(perms)


async def test_editor(app: FastAPI, demo: None) -> None:
    async with user_client(app, "editor@example.com", "editor") as c:
        r = await get(c, "demo")

    assert r.status_code == 200, r.text
    body = r.json()
    assert not {"org.manage", "member.manage", "audit.read"} & set(body["org"])
    assert "flag.create" in body["project"]
    assert "audit.read" not in body["project"]


async def test_viewer(app: FastAPI, demo: None) -> None:
    async with user_client(app, "viewer@example.com", "viewer") as c:
        r = await get(c, "demo")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["org"] == ["audit.read", "project.read", "evaluate", "sdk_key.read"]
    assert "project.manage" not in body["project"]


async def test_project_scoped_binding(app: FastAPI, demo: None) -> None:
    async with user_client(app, "editor@example.com", "editor", "project", "demo") as c:
        r = await get(c, "demo")
        other = await get(c, "other")

    assert r.status_code == 200, r.text
    assert r.json()["org"] == []
    assert "flag.create" in r.json()["project"]
    assert_error(other, 404, "not_found")


async def test_env_scoped_binding(app: FastAPI, demo: None) -> None:
    async with user_client(
        app, "viewer@example.com", "viewer", "environment", "demo", "production"
    ) as c:
        r = await get(c, "demo")

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["environments"]) == {"production"}
    assert "project.read" in body["project"]


async def test_personal_token_is_capped(app: FastAPI, demo: None) -> None:
    token = {"name": "ci", "permissions": ["project.read", "flag.toggle"], "project": "demo"}
    async with user_client(app, "editor@example.com", "editor") as c:
        created = await c.post("/api/account/tokens", json=token)
    assert created.status_code == 201, created.text

    transport = httpx.ASGITransport(app=app)
    headers = bearer(created.json()["secret"])
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        r = await get(c, "demo")

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["project"]) <= {"project.read", "flag.toggle"}
    assert body["environments"]
    for perms in body["environments"].values():
        assert set(perms) <= {"flag.toggle"}


async def test_unknown_project_is_404(app: FastAPI, demo: None) -> None:
    async with user_client(app, "admin@example.com", "admin") as c:
        assert_error(await get(c, "nope"), 404, "not_found")


async def test_no_credentials_is_401(anon: httpx.AsyncClient) -> None:
    assert_error(await get(anon), 401, "unauthorized")


def test_route_is_guarded() -> None:
    assert ("GET", PERMISSIONS) not in PUBLIC_ROUTES

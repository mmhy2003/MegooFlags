"""Permissions on the project, environment, flag and segment routes (plan Appendix G)."""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    assert_error,
    bearer,
    create_flag,
    create_project,
    seed_binding,
)
from sqlalchemy import select

from megooflags_api.models import Environment, Project
from megooflags_api.services.api_tokens import create_service_token

DEMO_ENVS = [
    {"key": "development", "name": "Development"},
    {"key": "production", "name": "Production"},
    {"key": "staging", "name": "Staging"},
]
TOGGLE = "/api/projects/demo/flags/new-ui/environments/{env}/toggle"
COPY = "/api/projects/demo/flags/new-ui/copy"

# (role, project, env) per binding; a missing project means org scope.
Binding = tuple[str, str | None, str | None]
ClientAs = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
async def demo(api: httpx.AsyncClient) -> None:
    await create_project(api, "demo", DEMO_ENVS)
    await create_project(api, "other", DEMO_ENVS)
    await create_flag(api, "demo", "new-ui")
    r = await api.post("/api/projects/demo/segments", json={"key": "beta", "name": "Beta"})
    assert r.status_code == 201, r.text


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


@pytest.fixture
def client_as(app: FastAPI, demo: None) -> ClientAs:
    """A client with a new service token holding each `(role, project, env)` binding."""

    @asynccontextmanager
    async def factory(*bindings: Binding) -> AsyncIterator[httpx.AsyncClient]:
        async with app.state.sessionmaker() as session:
            row, token = await create_service_token(
                session, "rbac", created_by=None, expires_at=None
            )
            await session.commit()
        for role, project, env in bindings:
            if project is None:
                await seed_binding(app, "service_token", row.id, role)
                continue
            project_id, env_id = await scope_ids(app, project, env)
            scope = "project" if env_id is None else "environment"
            await seed_binding(app, "service_token", row.id, role, scope, project_id, env_id)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers=bearer(token)
        ) as client:
            yield client

    return factory


async def test_no_credentials_is_401(anon: httpx.AsyncClient) -> None:
    assert_error(await anon.get("/api/projects"), 401, "unauthorized")


async def test_org_viewer_reads_but_cannot_write(client_as: ClientAs) -> None:
    async with client_as(("viewer", None, None)) as viewer:
        r = await viewer.get("/api/projects/demo/flags")
        assert r.status_code == 200, r.text
        r = await viewer.post(
            "/api/projects/demo/flags", json={"key": "x", "name": "x", "type": "boolean"}
        )
        assert_error(r, 403, "forbidden")
        r = await viewer.post(TOGGLE.format(env="production"), json={"enabled": True})
        assert_error(r, 403, "forbidden")


async def test_production_editor_sees_and_changes_only_production(client_as: ClientAs) -> None:
    async with client_as(("editor", "demo", "production")) as editor:
        r = await editor.post(TOGGLE.format(env="production"), json={"enabled": True})
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is True
        # staging is hidden from this token, so it does not exist for it.
        r = await editor.post(TOGGLE.format(env="staging"), json={"enabled": True})
        assert_error(r, 404, "not_found")

        r = await editor.get("/api/projects")
        assert [p["key"] for p in r.json()["items"]] == ["demo"]
        r = await editor.get("/api/projects/demo/environments")
        assert [e["key"] for e in r.json()] == ["production"]
        assert_error(await editor.get("/api/projects/demo/environments/staging"), 404, "not_found")

        r = await editor.patch("/api/projects/demo/flags/new-ui", json={"name": "New"})
        assert_error(r, 403, "forbidden")


async def test_toggle_in_a_visible_env_without_the_permission_is_403(client_as: ClientAs) -> None:
    async with client_as(("viewer", "demo", None), ("editor", "demo", "production")) as editor:
        r = await editor.post(TOGGLE.format(env="production"), json={"enabled": True})
        assert r.status_code == 200, r.text
        r = await editor.post(TOGGLE.format(env="staging"), json={"enabled": True})
        assert_error(r, 403, "forbidden")


async def test_flag_environments_list_is_filtered(client_as: ClientAs) -> None:
    async with client_as(("editor", "demo", "production")) as editor:
        r = await editor.get("/api/projects/demo/flags/new-ui/environments")
        assert r.status_code == 200, r.text
        assert [s["environment"] for s in r.json()] == ["production"]
    async with client_as(("viewer", "demo", None)) as viewer:
        r = await viewer.get("/api/projects/demo/flags/new-ui/environments")
        assert [s["environment"] for s in r.json()] == ["development", "production", "staging"]


async def test_token_on_another_project_gets_404_on_demo(client_as: ClientAs) -> None:
    async with client_as(("admin", "other", None)) as other:
        for path in (
            "/api/projects/demo",
            "/api/projects/demo/environments",
            "/api/projects/demo/environments/production",
            "/api/projects/demo/environments/production/sdk-keys",
            "/api/projects/demo/flags",
            "/api/projects/demo/flags/new-ui",
            "/api/projects/demo/flags/new-ui/environments",
            "/api/projects/demo/flags/new-ui/environments/production",
            "/api/projects/demo/segments",
            "/api/projects/demo/segments/beta",
        ):
            assert_error(await other.get(path), 404, "not_found")
        for method, path, body in (
            ("PATCH", "/api/projects/demo", {"name": "x"}),
            ("DELETE", "/api/projects/demo", None),
            ("POST", "/api/projects/demo/flags", {"key": "x", "name": "x", "type": "boolean"}),
            ("POST", TOGGLE.format(env="production"), {"enabled": True}),
            ("POST", COPY, {"from": "development", "to": ["staging"]}),
            ("DELETE", "/api/projects/demo/segments/beta", None),
            (
                "POST",
                "/api/projects/demo/environments/production/evaluate",
                {"context": {"targetingKey": "u1"}},
            ),
        ):
            r = await other.request(method, path, json=body)
            assert_error(r, 404, "not_found")
        r = await other.get("/api/projects")
        assert [p["key"] for p in r.json()["items"]] == ["other"]


async def test_deleting_a_project_needs_project_manage(
    api: httpx.AsyncClient, client_as: ClientAs
) -> None:
    # Everything an editor holds at project scope except project.manage.
    r = await api.post(
        "/api/roles",
        json={
            "key": "flag-editor",
            "name": "Flag editor",
            "permissions": ["project.read", "flag.create", "flag.update", "flag.archive"],
        },
    )
    assert r.status_code == 201, r.text
    async with client_as(("flag-editor", "demo", None)) as flag_editor:
        assert_error(await flag_editor.delete("/api/projects/demo"), 403, "forbidden")
    async with client_as(("viewer", "demo", None)) as viewer:
        assert_error(await viewer.delete("/api/projects/demo"), 403, "forbidden")
    # The built-in editor holds project.manage, so a project editor may delete its project.
    async with client_as(("editor", "demo", None)) as editor:
        assert (await editor.delete("/api/projects/demo")).status_code == 204


async def test_copy_needs_config_write_on_every_target(client_as: ClientAs) -> None:
    async with client_as(("viewer", "demo", None), ("editor", "demo", "production")) as editor:
        r = await editor.post(COPY, json={"from": "development", "to": ["staging", "production"]})
        assert_error(r, 403, "forbidden")
        r = await editor.post(COPY, json={"from": "development", "to": ["production"]})
        assert r.status_code == 200, r.text
        assert [s["environment"] for s in r.json()] == ["production"]


async def test_copy_into_a_hidden_target_is_404(client_as: ClientAs) -> None:
    async with client_as(("editor", "demo", "production"), ("editor", "demo", "development")) as c:
        r = await c.post(COPY, json={"from": "development", "to": ["staging", "production"]})
        assert_error(r, 404, "not_found")


async def test_copy_from_a_hidden_source_is_404(client_as: ClientAs) -> None:
    async with client_as(("editor", "demo", "production")) as editor:
        r = await editor.post(COPY, json={"from": "staging", "to": ["production"]})
        assert_error(r, 404, "not_found")

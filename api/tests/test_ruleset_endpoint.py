"""The ruleset download for API tokens: the same bytes and ETag as /sdk/v1/ruleset."""

import uuid

import httpx
from fastapi import FastAPI
from helpers import (
    assert_error,
    bearer,
    create_flag,
    create_project,
    create_sdk_key,
    seed_user,
    sign_in,
)
from sqlalchemy import select

from megooflags_api.models import Environment, Project

RULESET = "/api/projects/demo/environments/{env}/ruleset"
SDK_RULESET = "/sdk/v1/ruleset"


async def setup_demo(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api, key="new-ui")


async def development(app: FastAPI) -> tuple[uuid.UUID, uuid.UUID]:
    """The ids of project `demo` and its `development` environment."""
    async with app.state.sessionmaker() as session:
        row = await session.execute(
            select(Project.id, Environment.id)
            .join(Environment, Environment.project_id == Project.id)
            .where(Project.key == "demo", Environment.key == "development")
        )
        project_id, environment_id = row.one()
        return project_id, environment_id


async def test_body_and_etag_match_sdk_ruleset(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "server"))["key"]
    sdk = await api.get(SDK_RULESET, headers=bearer(key))
    r = await api.get(RULESET.format(env="production"))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/json"
    assert r.headers["cache-control"] == "no-cache"
    assert r.content == sdk.content
    assert r.headers["etag"] == sdk.headers["etag"]


async def test_if_none_match_304(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    etag = (await api.get(RULESET.format(env="production"))).headers["etag"]
    r = await api.get(RULESET.format(env="production"), headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.headers["etag"] == etag
    assert r.content == b""


async def test_viewer_can_read(
    app: FastAPI, api: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    await setup_demo(api)
    await seed_user(app, "viewer@example.com", role="viewer")
    assert (await sign_in(anon, "viewer@example.com")).status_code == 200
    r = await anon.get(RULESET.format(env="production"))
    assert r.status_code == 200, r.text
    assert "new-ui" in r.json()["flags"]


async def test_hidden_environment_404(
    app: FastAPI, api: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    await setup_demo(api)
    project_id, development_id = await development(app)
    await seed_user(
        app,
        "dev@example.com",
        role="viewer",
        scope="environment",
        project_id=project_id,
        environment_id=development_id,
    )
    assert (await sign_in(anon, "dev@example.com")).status_code == 200
    assert (await anon.get(RULESET.format(env="development"))).status_code == 200
    assert_error(await anon.get(RULESET.format(env="production")), 404, "not_found")


async def test_unknown_project_404(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/projects/nope/environments/production/ruleset")
    assert_error(r, 404, "not_found")


async def test_reflects_writes_at_once(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    before = (await api.get(RULESET.format(env="production"))).headers["etag"]
    r = await api.post(
        "/api/projects/demo/flags/new-ui/environments/development/toggle", json={"enabled": True}
    )
    assert r.status_code == 200, r.text
    assert (await api.get(RULESET.format(env="production"))).headers["etag"] == before
    r = await api.post(
        "/api/projects/demo/flags/new-ui/environments/production/toggle", json={"enabled": True}
    )
    assert r.status_code == 200, r.text
    after = await api.get(RULESET.format(env="production"))
    assert after.headers["etag"] != before
    assert after.json()["flags"]["new-ui"]["enabled"] is True

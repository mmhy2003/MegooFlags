import asyncio
import json
import os
import uuid
from typing import Any

import httpx
from fastapi import FastAPI
from helpers import count_statements, create_flag, create_project, create_sdk_key
from prometheus_client import REGISTRY
from sqlalchemy import select

from megooflags_api.main import create_app
from megooflags_api.models import Environment, Project
from megooflags_api.services.changes import ChangeBus
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.settings import Settings
from megooflags_api.valkey import create_valkey

UNREACHABLE = "redis://127.0.0.1:1/0"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EXCLUDED = {
    ("POST", "/api/projects/{project}/environments/{env}/evaluate"),
    ("POST", "/api/projects/{project}/environments/{env}/sdk-keys"),
    ("DELETE", "/api/projects/{project}/environments/{env}/sdk-keys/{id}"),
}
P = "/api/projects/{project}"
F = f"{P}/flags/{{flag}}"
FE = f"{F}/environments/{{env}}"
S = f"{P}/segments/{{segment}}"
PARAMS = {
    "project": "demo",
    "env": "production",
    "flag": "color",
    "variation": "c",
    "segment": "beta",
}

# (method, path template, path params, body, project key), in an order where every call succeeds.
WRITES: list[tuple[str, str, dict[str, str], Any, str]] = [
    ("POST", "/api/projects", {}, {"key": "demo", "name": "Demo"}, "demo"),
    ("PATCH", P, {}, {"name": "Demo 2"}, "demo"),
    ("POST", f"{P}/environments", {}, {"key": "qa", "name": "QA"}, "demo"),
    ("PATCH", f"{P}/environments/{{env}}", {"env": "qa"}, {"name": "Q A"}, "demo"),
    (
        "POST",
        f"{P}/flags",
        {},
        {
            "key": "color",
            "name": "Color",
            "type": "string",
            "variations": [{"key": "a", "value": "red"}, {"key": "b", "value": "blue"}],
            "off_variation": "a",
        },
        "demo",
    ),
    ("PATCH", F, {}, {"name": "Colour"}, "demo"),
    ("POST", f"{F}/variations", {}, {"key": "c", "value": "green"}, "demo"),
    ("PATCH", f"{F}/variations/{{variation}}", {}, {"name": "Green"}, "demo"),
    ("DELETE", f"{F}/variations/{{variation}}", {}, None, "demo"),
    ("PUT", FE, {}, {"version": 1, "enabled": False, "fallthrough": {"variation": "b"}}, "demo"),
    ("POST", f"{FE}/toggle", {}, {"enabled": True}, "demo"),
    ("POST", f"{FE}/kill", {}, None, "demo"),
    ("POST", f"{F}/copy", {}, {"from": "production", "to": ["staging"]}, "demo"),
    ("POST", f"{F}/archive", {}, None, "demo"),
    ("POST", f"{F}/restore", {}, None, "demo"),
    ("POST", f"{P}/segments", {}, {"key": "beta", "name": "Beta"}, "demo"),
    ("PUT", S, {}, {"included": ["u1"], "rules": []}, "demo"),
    ("PATCH", S, {}, {"name": "Beta testers"}, "demo"),
    ("DELETE", S, {}, None, "demo"),
    ("POST", f"{F}/archive", {}, None, "demo"),
    ("DELETE", F, {}, None, "demo"),
    ("DELETE", f"{P}/environments/{{env}}", {"env": "qa"}, None, "demo"),
    ("POST", "/api/projects", {}, {"key": "tmp", "name": "Tmp"}, "tmp"),
    ("DELETE", P, {"project": "tmp"}, None, "tmp"),
]


async def project_id(app: FastAPI, key: str) -> uuid.UUID | None:
    async with app.state.sessionmaker() as session:
        result: uuid.UUID | None = await session.scalar(
            select(Project.id).where(Project.key == key)
        )
        return result


def record_publishes(app: FastAPI) -> list[uuid.UUID]:
    published: list[uuid.UUID] = []

    async def publish(project_id: uuid.UUID) -> None:
        published.append(project_id)

    app.state.change_bus.publish = publish
    return published


async def test_every_write_route_publishes(app: FastAPI, api: httpx.AsyncClient) -> None:
    published = record_publishes(app)

    for method, template, params, body, project_key in WRITES:
        before = await project_id(app, project_key)
        url = template.format(**{**PARAMS, **params})
        count = len(published)
        r = await api.request(method, url, json=body)
        assert 200 <= r.status_code < 300, (method, url, r.text)
        expected = before or await project_id(app, project_key)
        assert published[count:] == [expected], (method, url)

    # app.routes hides included routers' APIRoutes (FastAPI 0.162), so read the OpenAPI paths.
    # Only project routes can change a ruleset; identity and auth routes publish nothing.
    routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/projects")
        for method in operations
        if method.upper() in WRITE_METHODS
    }
    assert routes - EXCLUDED == {(method, template) for method, template, *_ in WRITES}


async def test_publish_failure_does_not_fail_write(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    before = REGISTRY.get_sample_value("megooflags_change_publish_errors_total") or 0.0
    broken = create_app(Settings(valkey_url=UNREACHABLE))
    try:
        transport = httpx.ASGITransport(app=broken)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/projects/demo/flags/new-ui/environments/production/kill")
    finally:
        await broken.state.valkey.aclose()
        await broken.state.engine.dispose()

    assert r.status_code == 200, r.text
    after = REGISTRY.get_sample_value("megooflags_change_publish_errors_total")
    assert after is not None and after > before


async def test_publish_dispatches_locally() -> None:
    client = create_valkey(UNREACHABLE)
    bus = ChangeBus(client, UNREACHABLE)
    seen: list[uuid.UUID] = []
    bus.on_project.append(seen.append)
    project = uuid.uuid4()
    try:
        await bus.publish(project)
    finally:
        await client.aclose()

    assert seen == [project]


class Recorder:
    def __init__(self) -> None:
        self.ids: list[uuid.UUID] = []
        self.received = asyncio.Event()

    def __call__(self, id: uuid.UUID) -> None:
        self.ids.append(id)
        self.received.set()

    async def wait(self) -> None:
        async with asyncio.timeout(2):
            await self.received.wait()


async def test_round_trip(valkey_up: None) -> None:
    url = os.environ["MEGOOFLAGS_VALKEY_URL"]
    client_a, client_b = create_valkey(url), create_valkey(url)
    bus_a, bus_b = ChangeBus(client_a, url), ChangeBus(client_b, url)
    subscribed = asyncio.Event()

    async def on_resubscribe() -> None:
        subscribed.set()

    projects, keys = Recorder(), Recorder()
    bus_b.on_resubscribe.append(on_resubscribe)
    bus_b.on_project.append(projects)
    bus_b.on_key_revoked.append(keys)
    task = asyncio.create_task(bus_b.run())
    try:
        async with asyncio.timeout(2):
            await subscribed.wait()
        project, key = uuid.uuid4(), uuid.uuid4()
        await bus_a.publish(project)
        await projects.wait()
        await bus_a.publish_key_revoked(key)
        await keys.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client_a.aclose()
        await client_b.aclose()

    assert projects.ids == [project]
    assert keys.ids == [key]


def test_malformed_message_ignored() -> None:
    bus = ChangeBus(create_valkey(UNREACHABLE), UNREACHABLE)
    seen: list[uuid.UUID] = []
    bus.on_project.append(seen.append)
    bus.on_key_revoked.append(seen.append)

    for raw in (b"nope", '{"kind":"x"}', '{"kind":"project","id":"zz"}', "[1]", b"\xff"):
        bus.dispatch(raw)

    assert seen == []


async def test_revoke_publishes_key(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    key = await create_sdk_key(api, "server")
    revoked: list[uuid.UUID] = []

    async def publish_key_revoked(key_id: uuid.UUID) -> None:
        revoked.append(key_id)

    app.state.change_bus.publish_key_revoked = publish_key_revoked
    r = await api.delete(f"/api/projects/demo/environments/production/sdk-keys/{key['id']}")

    assert r.status_code == 204, r.text
    assert revoked == [uuid.UUID(key["id"])]


async def test_message_invalidates_cache(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    cache = app.state.ruleset_cache
    async with app.state.sessionmaker() as session:
        environment = EnvRef.of(
            (
                await session.scalars(
                    select(Environment)
                    .join(Project, Project.id == Environment.project_id)
                    .where(Project.key == "demo", Environment.key == "production")
                )
            ).one()
        )
        await cache.get_entry(session, environment, max_age=1)
        with count_statements(app.state.engine) as warm:
            await cache.get_entry(session, environment, max_age=1)
        assert warm == []

        app.state.change_bus.dispatch(
            json.dumps({"kind": "project", "id": str(environment.project_id)})
        )
        with count_statements(app.state.engine) as statements:
            await cache.get_entry(session, environment, max_age=1)

    assert len(statements) == 1

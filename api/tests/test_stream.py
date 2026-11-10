import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    SseEvent,
    create_project,
    create_sdk_key,
    next_events,
    running,
    seed_service_token,
)
from test_sdk import FLAGS, RULESET, bearer, setup_demo

from megooflags_api.evaluation import parse_ruleset
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.ruleset_cache import CachedRuleset, RulesetCache
from megooflags_api.services.sdk_keys import KeyResolver, SdkPrincipal
from megooflags_api.services.streams import Revoked, StreamHub, Subscriber

STREAM = "/sdk/v1/stream"

pytestmark = pytest.mark.usefixtures("valkey_up")


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """A served app and a client with an org admin service token for the management API."""
    headers = bearer(await seed_service_token(app))
    async with (
        running(app) as url,
        httpx.AsyncClient(base_url=url, timeout=10, headers=headers) as client,
    ):
        yield client


@asynccontextmanager
async def without_credentials(client: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    """A client for the same server that sends no credentials of its own."""
    async with httpx.AsyncClient(base_url=client.base_url, timeout=10) as plain:
        yield plain


@asynccontextmanager
async def stream(
    client: httpx.AsyncClient, key: str | None = None, **kwargs: object
) -> AsyncIterator[AsyncIterator[str]]:
    headers: dict[str, str] = dict(kwargs.pop("headers", {}))  # type: ignore[call-overload]
    if key is not None:
        headers.update(bearer(key))
    async with client.stream("GET", STREAM, headers=headers, **kwargs) as r:  # type: ignore[arg-type]
        assert r.status_code == 200, await r.aread()
        yield r.aiter_lines()


async def kill(client: httpx.AsyncClient) -> None:
    r = await client.post(f"{FLAGS}/color/environments/production/kill")
    assert r.status_code == 200, r.text


async def test_server_put_on_connect(app: FastAPI) -> None:
    async with serving(app) as client:
        await setup_demo(client)
        key = (await create_sdk_key(client, "server"))["key"]
        ruleset = await client.get(RULESET, headers=bearer(key))

        async with client.stream("GET", STREAM, headers=bearer(key)) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            assert r.headers["cache-control"] == "no-cache"
            assert r.headers["x-accel-buffering"] == "no"
            lines = r.aiter_lines()
            assert await anext(lines) == "retry: 3000"
            assert await anext(lines) == ""
            (put,) = await next_events(lines, 1, 2)

    assert put.event == "put"
    assert put.id == ruleset.headers["etag"]
    assert json.loads(put.data) == ruleset.json()


async def test_kill_pushes_put(app: FastAPI) -> None:
    async with serving(app) as client:
        await setup_demo(client)
        key = (await create_sdk_key(client, "server"))["key"]
        async with stream(client, key) as lines:
            (first,) = await next_events(lines, 1, 2)
            await kill(client)
            (put,) = await next_events(lines, 1, 2)

    assert json.loads(first.data)["flags"]["color"]["enabled"] is True
    assert put.event == "put"
    assert put.id != first.id
    assert json.loads(put.data)["flags"]["color"]["enabled"] is False


async def test_client_gets_changed(app: FastAPI) -> None:
    async with serving(app) as client:
        await setup_demo(client)
        key = (await create_sdk_key(client, "client"))["key"]
        async with (
            without_credentials(client) as plain,
            stream(plain, params={"key": key}) as lines,
        ):
            (first,) = await next_events(lines, 1, 2)
            await kill(client)
            (second,) = await next_events(lines, 1, 2)

    for event in (first, second):
        assert event.event == "changed"
        assert json.loads(event.data) == {"etag": event.id}
        assert "flags" not in event.data
    assert second.id != first.id


async def test_heartbeat(app: FastAPI) -> None:
    app.state.settings.sse_heartbeat_seconds = 0.2
    async with serving(app) as client:
        await setup_demo(client)
        key = (await create_sdk_key(client, "server"))["key"]
        async with stream(client, key) as lines:
            _, ping = await next_events(lines, 2, 2)

    assert ping == SseEvent(event=None, data="ping", id=None, comment=True)


async def test_last_event_id_skips_first(app: FastAPI) -> None:
    app.state.settings.sse_heartbeat_seconds = 0.2
    async with serving(app) as client:
        await setup_demo(client)
        key = (await create_sdk_key(client, "server"))["key"]
        etag = (await client.get(RULESET, headers=bearer(key))).headers["etag"]
        async with stream(client, key, headers={"Last-Event-ID": etag}) as lines:
            (first,) = await next_events(lines, 1, 2)

    assert first.comment and first.data == "ping"


async def revoked_then_closed(lines: AsyncIterator[str]) -> None:
    events = await next_events(lines, 1, 2)
    while events[-1].comment:  # heartbeats may come before the revocation is noticed
        events = await next_events(lines, 1, 2)
    assert events[-1].event == "revoked"
    assert events[-1].data == "{}"
    rest = [line async for line in lines]
    assert rest in ([], [""])


async def test_revoke_closes(app: FastAPI) -> None:
    async with serving(app) as client:
        await setup_demo(client)
        created = await create_sdk_key(client, "server")
        async with stream(client, created["key"]) as lines:
            await next_events(lines, 1, 2)
            r = await client.delete(
                f"/api/projects/demo/environments/production/sdk-keys/{created['id']}"
            )
            assert r.status_code == 204
            await revoked_then_closed(lines)


async def test_revoked_by_heartbeat_without_valkey_message(app: FastAPI) -> None:
    async def no_publish(key_id: uuid.UUID) -> None:
        pass

    app.state.change_bus.publish_key_revoked = no_publish
    app.state.key_resolver = KeyResolver(ttl=0)
    app.state.settings.sse_heartbeat_seconds = 0.2
    async with serving(app) as client:
        await setup_demo(client)
        created = await create_sdk_key(client, "server")
        async with stream(client, created["key"]) as lines:
            await next_events(lines, 1, 2)
            r = await client.delete(
                f"/api/projects/demo/environments/production/sdk-keys/{created['id']}"
            )
            assert r.status_code == 204
            await revoked_then_closed(lines)


async def test_environment_delete_revokes(app: FastAPI) -> None:
    async with serving(app) as client:
        await setup_demo(client)
        key = (await create_sdk_key(client, "server"))["key"]
        async with stream(client, key) as lines:
            await next_events(lines, 1, 2)
            r = await client.delete("/api/projects/demo/environments/production")
            assert r.status_code == 204
            await revoked_then_closed(lines)


async def test_server_key_in_query_401(app: FastAPI) -> None:
    async with serving(app) as client:
        await create_project(client)
        key = (await create_sdk_key(client, "server"))["key"]
        async with without_credentials(client) as plain:
            r = await plain.get(STREAM, params={"key": key})

    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


async def test_no_key_401(app: FastAPI) -> None:
    async with serving(app) as client, without_credentials(client) as plain:
        r = await plain.get(STREAM)

    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


def principal(project_id: uuid.UUID | None = None) -> SdkPrincipal:
    env = EnvRef(id=uuid.uuid4(), project_id=project_id or uuid.uuid4(), key="production")
    return SdkPrincipal(uuid.uuid4(), SdkKeyKind.SERVER, "hash", env, revoked=False)


def cached(etag: str) -> CachedRuleset:
    raw = {"schema_version": 1, "project": "p", "environment": "e", "flags": {}, "segments": {}}
    return CachedRuleset(fingerprint=etag, ruleset=parse_ruleset(raw), body=b"{}", etag=etag)


async def test_subscriber_keeps_latest() -> None:
    sub = Subscriber(principal())
    a, b = cached('"a"'), cached('"b"')

    sub.offer(a)
    sub.offer(b)

    assert sub.queue.qsize() == 1
    assert sub.queue.get_nowait() is b
    sub.offer(Revoked())
    assert sub.queue.get_nowait() == Revoked()


async def test_burst_merges() -> None:
    hub = StreamHub(None, RulesetCache())  # type: ignore[arg-type]
    p = principal()
    hub.subscribe(p)
    calls: list[set[uuid.UUID]] = []

    async def refresh(env_ids: set[uuid.UUID]) -> None:
        calls.append(set(env_ids))

    hub.refresh = refresh  # type: ignore[method-assign]
    for _ in range(5):
        hub.notify_project(p.environment.project_id)
        await asyncio.sleep(0.01)
    hub.notify_project(uuid.uuid4())  # another project: nothing subscribed, nothing to do
    await asyncio.sleep(0.3)

    assert calls == [{p.environment.id}]

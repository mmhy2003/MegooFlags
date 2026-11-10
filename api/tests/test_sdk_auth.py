import asyncio
import time
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from helpers import assert_error, count_statements, create_project, create_sdk_key
from prometheus_client import REGISTRY

from megooflags_api.main import create_app
from megooflags_api.sdk_auth import AnyKeyDep, OfrepKeyDep, ServerKeyDep, StreamKeyDep
from megooflags_api.services.rate_limit import RateLimiter
from megooflags_api.settings import Settings

KEYS = "/api/projects/demo/environments/production/sdk-keys"


def add_test_routes(app: FastAPI) -> None:
    r = APIRouter()

    @r.get("/t/server")
    async def s(p: ServerKeyDep) -> dict[str, str]:
        return {"kind": p.kind, "env": p.environment.key}

    @r.get("/t/any")
    async def a(p: AnyKeyDep) -> dict[str, str]:
        return {"kind": p.kind, "env": p.environment.key}

    @r.get("/t/ofrep")
    async def o(p: OfrepKeyDep) -> dict[str, str]:
        return {"kind": p.kind, "env": p.environment.key}

    @r.get("/t/stream")
    async def st(p: StreamKeyDep) -> dict[str, str]:
        return {"kind": p.kind, "env": p.environment.key}

    app.include_router(r)


@pytest.fixture
async def sdk(app: FastAPI, api: httpx.AsyncClient) -> httpx.AsyncClient:
    add_test_routes(app)
    await create_project(api)
    return api


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_missing_header_401(sdk: httpx.AsyncClient) -> None:
    r = await sdk.get("/t/server")

    assert_error(r, 401, "unauthorized")
    assert r.headers["WWW-Authenticate"] == "Bearer"


async def test_garbage_key_401_without_query(app: FastAPI, sdk: httpx.AsyncClient) -> None:
    with count_statements(app.state.engine) as statements:
        r = await sdk.get("/t/server", headers=bearer("not-a-key"))

    assert_error(r, 401, "unauthorized")
    assert statements == []


async def test_unknown_well_formed_key_401(sdk: httpx.AsyncClient) -> None:
    r = await sdk.get("/t/server", headers=bearer("mf_server_" + "a" * 32))

    assert_error(r, 401, "unauthorized")


async def test_scheme_case_insensitive(sdk: httpx.AsyncClient) -> None:
    key = (await create_sdk_key(sdk, "server"))["key"]

    r = await sdk.get("/t/server", headers={"Authorization": f"bearer {key}"})

    assert r.status_code == 200, r.text


async def test_server_and_client_keys(sdk: httpx.AsyncClient) -> None:
    server = (await create_sdk_key(sdk, "server"))["key"]
    client = (await create_sdk_key(sdk, "client"))["key"]

    r = await sdk.get("/t/server", headers=bearer(server))
    assert r.status_code == 200, r.text
    assert r.json() == {"kind": "server", "env": "production"}
    assert_error(await sdk.get("/t/server", headers=bearer(client)), 403, "forbidden")
    for key, kind in ((server, "server"), (client, "client")):
        r = await sdk.get("/t/any", headers=bearer(key))
        assert r.status_code == 200, r.text
        assert r.json() == {"kind": kind, "env": "production"}


async def test_revoked_before_first_use_403(sdk: httpx.AsyncClient) -> None:
    created = await create_sdk_key(sdk, "server")
    assert (await sdk.delete(f"{KEYS}/{created['id']}")).status_code == 204

    assert_error(await sdk.get("/t/server", headers=bearer(created["key"])), 403, "forbidden")


async def test_revoked_after_use_cached_until_forgotten(
    app: FastAPI, sdk: httpx.AsyncClient
) -> None:
    created = await create_sdk_key(sdk, "server")
    headers = bearer(created["key"])
    assert (await sdk.get("/t/server", headers=headers)).status_code == 200

    assert (await sdk.delete(f"{KEYS}/{created['id']}")).status_code == 204
    assert (await sdk.get("/t/server", headers=headers)).status_code == 200
    app.state.key_resolver.forget(uuid.UUID(created["id"]))

    assert_error(await sdk.get("/t/server", headers=headers), 403, "forbidden")


async def test_resolver_hit_issues_no_statements(app: FastAPI, sdk: httpx.AsyncClient) -> None:
    headers = bearer((await create_sdk_key(sdk, "server"))["key"])
    with count_statements(app.state.engine) as first:
        assert (await sdk.get("/t/server", headers=headers)).status_code == 200
    with count_statements(app.state.engine) as second:
        assert (await sdk.get("/t/server", headers=headers)).status_code == 200

    assert len(first) == 1
    assert second == []


async def test_ofrep_accepts_x_api_key(sdk: httpx.AsyncClient) -> None:
    server = (await create_sdk_key(sdk, "server"))["key"]
    client = (await create_sdk_key(sdk, "client"))["key"]

    r = await sdk.get("/t/ofrep", headers={"X-API-Key": client})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "client"
    r = await sdk.get("/t/ofrep", headers={"X-API-Key": client, **bearer(server)})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "server"
    assert_error(await sdk.get("/t/ofrep"), 401, "unauthorized")


async def test_stream_query_key_client_only(sdk: httpx.AsyncClient) -> None:
    server = (await create_sdk_key(sdk, "server"))["key"]
    client = (await create_sdk_key(sdk, "client"))["key"]

    r = await sdk.get("/t/stream", params={"key": client})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "client"
    assert_error(await sdk.get("/t/stream", params={"key": server}), 401, "unauthorized")
    r = await sdk.get("/t/stream", headers=bearer(server))
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "server"


async def wait_for_fresh_window(margin: float = 5.0) -> None:
    """Keep a multi-request test inside one fixed window."""
    into = time.time() % 60
    if into > 60 - margin:
        await asyncio.sleep(60 - into + 0.1)


@pytest.fixture
async def limited_pair(sdk: httpx.AsyncClient) -> AsyncIterator[list[httpx.AsyncClient]]:
    apps = [create_app(Settings(client_rate_limit_per_minute=3)) for _ in range(2)]
    clients = []
    for a in apps:
        add_test_routes(a)
        clients.append(
            httpx.AsyncClient(transport=httpx.ASGITransport(app=a), base_url="http://test")
        )
    try:
        yield clients
    finally:
        for c, a in zip(clients, apps, strict=True):
            await c.aclose()
            await a.state.valkey.aclose()
            await a.state.engine.dispose()


async def test_rate_limit_shared_across_apps(
    valkey_up: None, sdk: httpx.AsyncClient, limited_pair: list[httpx.AsyncClient]
) -> None:
    headers = bearer((await create_sdk_key(sdk, "client"))["key"])
    await wait_for_fresh_window()

    for i in range(3):
        r = await limited_pair[i % 2].get("/t/any", headers=headers)
        assert r.status_code == 200, r.text
    r = await limited_pair[1].get("/t/any", headers=headers)

    assert_error(r, 429, "rate_limited")
    assert 1 <= int(r.headers["Retry-After"]) <= 60


async def test_server_keys_not_limited(
    valkey_up: None, sdk: httpx.AsyncClient, limited_pair: list[httpx.AsyncClient]
) -> None:
    headers = bearer((await create_sdk_key(sdk, "server"))["key"])
    await wait_for_fresh_window()

    for i in range(10):
        r = await limited_pair[i % 2].get("/t/any", headers=headers)
        assert r.status_code == 200, r.text


async def test_rate_limit_fails_open(sdk: httpx.AsyncClient) -> None:
    headers = bearer((await create_sdk_key(sdk, "client"))["key"])
    before = REGISTRY.get_sample_value("megooflags_rate_limit_errors_total") or 0.0
    app = create_app(Settings(valkey_url="redis://127.0.0.1:1/0"))
    add_test_routes(app)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/t/any", headers=headers)
    finally:
        await app.state.valkey.aclose()
        await app.state.engine.dispose()

    assert r.status_code == 200, r.text
    after = REGISTRY.get_sample_value("megooflags_rate_limit_errors_total")
    assert after is not None and after > before


async def test_rate_limiter_window_rolls_over(valkey_up: None) -> None:
    app = create_app(Settings())
    now = 1_000_000 * 60 + 58.5
    limiter = RateLimiter(app.state.valkey, 2, clock=lambda: now)
    key_id = uuid.uuid4()
    try:
        assert await limiter.hit(key_id, "10.0.0.1") is None
        assert await limiter.hit(key_id, "10.0.0.1") is None
        assert await limiter.hit(key_id, "10.0.0.1") == 2
        assert await limiter.hit(key_id, "10.0.0.2") is None
        now = 1_000_001 * 60 + 0.5
        assert await limiter.hit(key_id, "10.0.0.1") is None
    finally:
        await app.state.valkey.aclose()
        await app.state.engine.dispose()

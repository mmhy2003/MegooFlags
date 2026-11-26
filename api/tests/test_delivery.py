import json
import uuid
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from typing import Any

import httpx
import pytest
from conftest import close_app
from fastapi import FastAPI
from helpers import assert_error
from starlette.requests import Request

from megooflags_api.main import create_app
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.routers.sdk import RETRY_FRAME, REVOKED_FRAME, _events
from megooflags_api.schemas.events import EventsIn
from megooflags_api.services.delivery import Delivery
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.ruleset_cache import CachedRuleset, _prepare, from_body
from megooflags_api.services.sdk_keys import SdkPrincipal
from megooflags_api.services.streams import Closed, Revoked, StreamHub, Subscriber
from megooflags_api.settings import Settings

ENV = EnvRef(id=uuid.uuid4(), project_id=uuid.uuid4(), key="production")
SERVER_KEY = "mf_server_" + "s" * 32
CLIENT_KEY = "mf_client_" + "c" * 32
RAW: dict[str, Any] = {
    "schema_version": 1,
    "project": "shop",
    "environment": "production",
    "flags": {
        "banner": {
            "key": "banner",
            "type": "boolean",
            "enabled": True,
            "version": 2,
            "variations": [{"key": "on", "value": True}, {"key": "off", "value": False}],
            "off_variation": "off",
            "prerequisites": [],
            "targets": [],
            "rules": [],
            "fallthrough": {"variation": "on"},
        }
    },
    "segments": {},
}
BODY = json.dumps(RAW, separators=(",", ":")).encode()


def principal(kind: SdkKeyKind, secret_hash: str) -> SdkPrincipal:
    return SdkPrincipal(uuid.uuid4(), kind, secret_hash, ENV, revoked=False)


class FakeRulesets:
    def __init__(self) -> None:
        self.entry = from_body(BODY)
        self.reads: list[EnvRef] = []

    async def get_entry(self, environment: EnvRef) -> CachedRuleset:
        self.reads.append(environment)
        return self.entry


class FakeKeys:
    def __init__(self) -> None:
        self.by_key = {
            SERVER_KEY: principal(SdkKeyKind.SERVER, "server-hash"),
            CLIENT_KEY: principal(SdkKeyKind.CLIENT, "client-hash"),
        }

    async def resolve(self, key: str) -> SdkPrincipal | None:
        return self.by_key.get(key)

    async def resolve_hash(self, secret_hash: str) -> SdkPrincipal | None:
        return next((p for p in self.by_key.values() if p.secret_hash == secret_hash), None)


class FakeSink:
    def __init__(self) -> None:
        self.counts: list[tuple[uuid.UUID, list[tuple[str, str | None]]]] = []
        self.stored: list[tuple[uuid.UUID, EventsIn, datetime]] = []

    def record_counts(
        self, environment_id: uuid.UUID, results: Iterable[tuple[str, str | None]]
    ) -> None:
        self.counts.append((environment_id, list(results)))

    async def store(self, environment_id: uuid.UUID, body: EventsIn, now: datetime) -> None:
        self.stored.append((environment_id, body, now))


class FakeLimiter:
    def __init__(self, retry: int | None = None) -> None:
        self.retry = retry

    async def hit(self, key_id: uuid.UUID, client_ip: str) -> int | None:
        return self.retry


class FakeStreamSource:
    def __init__(self, item: CachedRuleset | None) -> None:
        self.item = item
        self.inactive: set[uuid.UUID] = set()

    async def current(self, environment: EnvRef) -> CachedRuleset | None:
        return self.item

    async def inactive_keys(self, key_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        return key_ids & self.inactive


def fake_delivery(limiter: FakeLimiter | None = None) -> Delivery:
    return Delivery(FakeRulesets(), FakeKeys(), FakeSink(), limiter or FakeLimiter())


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    app = create_app(Settings())
    app.state.delivery = fake_delivery()
    try:
        yield app
    finally:
        await close_app(app)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_ruleset_served_from_source(app: FastAPI, client: httpx.AsyncClient) -> None:
    entry = app.state.delivery.rulesets.entry
    r = await client.get("/sdk/v1/ruleset", headers=bearer(SERVER_KEY))
    assert r.status_code == 200
    assert r.content == BODY
    assert r.headers["etag"] == entry.etag
    assert app.state.delivery.rulesets.reads == [ENV]

    r = await client.get(
        "/sdk/v1/ruleset", headers={**bearer(SERVER_KEY), "If-None-Match": entry.etag}
    )
    assert r.status_code == 304


async def test_evaluate_uses_source_and_records_counts(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    r = await client.post(
        "/sdk/v1/evaluate", headers=bearer(CLIENT_KEY), json={"context": {"key": "u1"}}
    )
    assert r.status_code == 200, r.text
    (result,) = r.json()["results"]
    assert (result["key"], result["value"], result["variant"]) == ("banner", True, "on")
    assert app.state.delivery.events.counts == [(ENV.id, [("banner", "on")])]


async def test_ofrep_uses_source(app: FastAPI, client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/ofrep/v1/evaluate/flags/banner",
        headers={"X-API-Key": SERVER_KEY},
        json={"context": {"targetingKey": "u1"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] is True
    r = await client.post("/ofrep/v1/evaluate/flags", headers=bearer(CLIENT_KEY), json={})
    assert [f["key"] for f in r.json()["flags"]] == ["banner"]
    assert app.state.delivery.events.counts == [
        (ENV.id, [("banner", "on")]),
        (ENV.id, [("banner", "on")]),
    ]


async def test_events_go_to_sink(app: FastAPI, client: httpx.AsyncClient) -> None:
    body = {
        "counts": [
            {"flag_key": "banner", "variant": "on", "hour": "2026-11-10T10:00:00Z", "count": 3}
        ]
    }
    r = await client.post("/sdk/v1/events", headers=bearer(SERVER_KEY), json=body)
    assert r.status_code == 202, r.text
    ((env_id, stored, _),) = app.state.delivery.events.stored
    assert env_id == ENV.id
    assert stored.counts[0].count == 3


async def test_unknown_key_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/sdk/v1/ruleset", headers=bearer("mf_server_" + "x" * 32))
    assert_error(r, 401, "unauthorized")


async def test_client_key_rate_limited(app: FastAPI, client: httpx.AsyncClient) -> None:
    app.state.delivery = fake_delivery(FakeLimiter(7))
    r = await client.post("/sdk/v1/evaluate", headers=bearer(CLIENT_KEY), json={"context": {}})
    assert_error(r, 429, "rate_limited")
    assert r.headers["retry-after"] == "7"
    # Server keys are never limited.
    r = await client.post("/sdk/v1/evaluate", headers=bearer(SERVER_KEY), json={"context": {}})
    assert r.status_code == 200


def test_from_body_etag_matches_prepare() -> None:
    cached = _prepare(RAW, "fingerprint")
    entry = from_body(cached.body)
    assert entry.etag == cached.etag
    assert entry.fingerprint == entry.etag
    assert entry.body == cached.body
    assert entry.ruleset == cached.ruleset


def test_from_body_sorted_keys() -> None:
    entry = from_body(BODY)
    assert entry.ruleset.flags
    assert entry.sorted_keys == tuple(sorted(entry.ruleset.flags))


async def test_stream_hub_with_source() -> None:
    entry = from_body(BODY)
    source = FakeStreamSource(entry)
    hub = StreamHub(None, None, source=source)  # type: ignore[arg-type]
    p = principal(SdkKeyKind.SERVER, "h")
    sub = hub.subscribe(p)

    await hub.refresh([ENV.id])
    assert sub.queue.get_nowait() == entry

    source.item = None
    await hub.refresh([ENV.id])
    assert isinstance(sub.queue.get_nowait(), Revoked)

    other = hub.subscribe(principal(SdkKeyKind.SERVER, "h2"))
    source.item = entry
    source.inactive = {other.principal.key_id}
    await hub.recheck_all()
    assert isinstance(other.queue.get_nowait(), Revoked)


async def test_close_all_ends_without_frame(app: FastAPI) -> None:
    sub = Subscriber(principal(SdkKeyKind.SERVER, "h"))
    sub.offer(Closed())
    sub.offer(from_body(BODY))
    assert isinstance(sub.queue.get_nowait(), Closed)

    hub = StreamHub(None, None, source=FakeStreamSource(None))  # type: ignore[arg-type]
    app.state.stream_hub = hub
    request = Request({"type": "http", "app": app})
    p = app.state.delivery.keys.by_key[SERVER_KEY]
    frames = _events(request, p, None)
    assert await anext(frames) == RETRY_FRAME
    assert b"event: put" in await anext(frames)
    hub.close_all()
    rest = [frame async for frame in frames]
    assert REVOKED_FRAME not in rest
    assert rest == []
    assert hub.subscriber_count == 0

    sub = hub.subscribe(p)
    hub.revoke_all()
    assert isinstance(sub.queue.get_nowait(), Revoked)

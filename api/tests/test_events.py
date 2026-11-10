import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI
from helpers import assert_error, count_statements, create_project, create_sdk_key
from sqlalchemy import select, text
from test_ofrep import setup_demo

from megooflags_api.models.project import Environment
from megooflags_api.models.sdk import EvaluationCount, ExposureEvent
from megooflags_api.services.events import (
    BODY_TOO_LARGE,
    MAX_BODY_BYTES,
    RETENTION_LOCK_ID,
    EvaluationCounter,
    EventsBodyLimitMiddleware,
    delete_expired,
)
from megooflags_api.settings import Settings

EVENTS = "/sdk/v1/events"


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def exposure(flag_key: str = "color", **extra: Any) -> dict[str, Any]:
    return {
        "flag_key": flag_key,
        "variant": "red",
        "reason": "TARGETING_MATCH",
        "timestamp": "2026-11-10T10:17:00Z",
        **extra,
    }


async def server_key(api: httpx.AsyncClient) -> str:
    await create_project(api)
    return (await create_sdk_key(api, "server"))["key"]  # type: ignore[no-any-return]


async def environment_id(app: FastAPI) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        env_id = await session.scalar(select(Environment.id).where(Environment.key == "production"))
    assert env_id is not None
    return env_id


async def exposures(app: FastAPI) -> list[ExposureEvent]:
    async with app.state.sessionmaker() as session:
        return list(await session.scalars(select(ExposureEvent).order_by(ExposureEvent.id)))


async def counts(app: FastAPI) -> list[EvaluationCount]:
    async with app.state.sessionmaker() as session:
        rows = await session.scalars(
            select(EvaluationCount).order_by(EvaluationCount.flag_key, EvaluationCount.variant)
        )
        return list(rows)


async def test_bulk_insert(app: FastAPI, api: httpx.AsyncClient) -> None:
    key = await server_key(api)
    env_id = await environment_id(app)
    body = {
        "exposures": [
            exposure(context={"key": "u1", "email": "a@example.com"}),
            exposure("size", variant=None, reason="DEFAULT"),
            exposure("beta", variant="on", reason="FALLTHROUGH"),
        ]
    }

    r = await api.post(EVENTS, json=body, headers=bearer(key))

    assert r.status_code == 202, r.text
    assert r.content == b""
    rows = await exposures(app)
    assert [(e.environment_id, e.flag_key, e.variant, e.reason) for e in rows] == [
        (env_id, "color", "red", "TARGETING_MATCH"),
        (env_id, "size", None, "DEFAULT"),
        (env_id, "beta", "on", "FALLTHROUGH"),
    ]
    assert all(e.occurred_at == datetime(2026, 11, 10, 10, 17, tzinfo=UTC) for e in rows)
    assert all(e.context is None for e in rows)


async def test_context_kept_when_enabled(app: FastAPI, api: httpx.AsyncClient) -> None:
    app.state.settings = Settings(persist_context=True)
    key = await server_key(api)

    r = await api.post(
        EVENTS, json={"exposures": [exposure(context={"key": "u1"})]}, headers=bearer(key)
    )

    assert r.status_code == 202, r.text
    assert [e.context for e in await exposures(app)] == [{"key": "u1"}]


async def test_future_timestamp_clamped(app: FastAPI, api: httpx.AsyncClient) -> None:
    key = await server_key(api)
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    r = await api.post(
        EVENTS, json={"exposures": [exposure(timestamp=future)]}, headers=bearer(key)
    )

    assert r.status_code == 202, r.text
    [row] = await exposures(app)
    assert row.occurred_at <= row.received_at


async def test_unknown_flag_accepted(app: FastAPI, api: httpx.AsyncClient) -> None:
    key = await server_key(api)

    r = await api.post(EVENTS, json={"exposures": [exposure("no-such-flag")]}, headers=bearer(key))

    assert r.status_code == 202, r.text
    assert [e.flag_key for e in await exposures(app)] == ["no-such-flag"]


async def test_counts_upsert(app: FastAPI, api: httpx.AsyncClient) -> None:
    key = await server_key(api)
    count = {"flag_key": "color", "variant": "red", "hour": "2026-11-10T10:17:00Z", "count": 2}
    body = {
        "counts": [
            count,
            {**count, "count": 3},
            {**count, "hour": "2026-11-10T15:47:00+05:30", "count": 4},  # 10:17Z
            {"flag_key": "size", "variant": None, "hour": "2026-11-10T10:59:59Z", "count": 1},
        ]
    }

    r = await api.post(EVENTS, json=body, headers=bearer(key))

    assert r.status_code == 202, r.text
    ten = datetime(2026, 11, 10, 10, tzinfo=UTC)
    assert [(c.flag_key, c.variant, c.hour, c.count) for c in await counts(app)] == [
        ("color", "red", ten, 9),
        ("size", "", ten, 1),
    ]

    r = await api.post(EVENTS, json={"counts": [count]}, headers=bearer(key))

    assert r.status_code == 202, r.text
    assert [(c.flag_key, c.count) for c in await counts(app)] == [("color", 11), ("size", 1)]


async def test_limits(app: FastAPI, api: httpx.AsyncClient) -> None:
    key = await server_key(api)

    r = await api.post(EVENTS, json={"exposures": [exposure()] * 1001}, headers=bearer(key))
    assert_error(r, 422, "validation_error")

    big = [exposure(context={"blob": "x" * 1200}) for _ in range(900)]
    r = await api.post(EVENTS, json={"exposures": big}, headers=bearer(key))
    assert_error(r, 413, "payload_too_large")

    zero = {"flag_key": "color", "variant": "red", "hour": "2026-11-10T10:00:00Z", "count": 0}
    r = await api.post(EVENTS, json={"counts": [zero]}, headers=bearer(key))
    assert_error(r, 422, "validation_error")

    assert await exposures(app) == []
    assert await counts(app) == []


async def test_client_key_allowed_and_cors(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    key = (await create_sdk_key(api, "client"))["key"]

    r = await api.post(
        EVENTS,
        json={"exposures": [exposure()]},
        headers={**bearer(key), "Origin": "https://app.example"},
    )

    assert r.status_code == 202, r.text
    assert r.headers["access-control-allow-origin"] == "*"
    assert len(await exposures(app)) == 1


async def test_events_require_a_key(app: FastAPI, api: httpx.AsyncClient) -> None:
    r = await api.post(EVENTS, json={"exposures": [exposure()]})

    assert_error(r, 401, "unauthorized")


async def test_oversized_body_without_a_key_is_unauthorized(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    big = [exposure(context={"blob": "x" * 1200}) for _ in range(900)]

    r = await api.post(EVENTS, json={"exposures": big})

    assert_error(r, 401, "unauthorized")


async def test_remote_evaluations_counted(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "client"))["key"]
    for _ in range(2):
        r = await api.post(
            "/sdk/v1/evaluate",
            json={"context": {"key": "u2"}, "flags": ["color", "missing"]},
            headers=bearer(key),
        )
        assert r.status_code == 200, r.text
    r = await api.post(
        "/ofrep/v1/evaluate/flags/color",
        json={"context": {"targetingKey": "u1"}},
        headers=bearer(key),
    )
    assert r.status_code == 200, r.text
    r = await api.post(
        "/ofrep/v1/evaluate/flags/missing", json={"context": {}}, headers=bearer(key)
    )
    assert r.status_code == 404, r.text

    await app.state.evaluation_counter.flush(app.state.sessionmaker)

    rows = await counts(app)
    assert {(c.flag_key, c.variant): c.count for c in rows} == {
        ("color", "red"): 2,
        ("color", "blue"): 1,
    }
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    assert {c.hour for c in rows} <= {hour, hour - timedelta(hours=1)}


async def test_ofrep_bulk_counted(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "server"))["key"]

    r = await api.post(
        "/ofrep/v1/evaluate/flags", json={"context": {"targetingKey": "u2"}}, headers=bearer(key)
    )
    assert r.status_code == 200, r.text
    await app.state.evaluation_counter.flush(app.state.sessionmaker)

    assert {(c.flag_key, c.variant, c.count) for c in await counts(app)} == {
        ("color", "red", 1),
        ("gated", "false", 1),
    }


async def test_flush_empty_no_statements(app: FastAPI) -> None:
    with count_statements(app.state.engine) as statements:
        await app.state.evaluation_counter.flush(app.state.sessionmaker)

    assert statements == []


async def test_cancelled_flush_keeps_the_batch(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    env_id = await environment_id(app)
    hour = datetime(2026, 11, 10, 10, tzinfo=UTC)
    counter = EvaluationCounter(clock=lambda: hour)
    counter.record(env_id, [("color", "red")])
    entered = asyncio.Event()

    @asynccontextmanager
    async def stuck() -> AsyncIterator[None]:
        entered.set()
        await asyncio.Event().wait()
        yield

    flush = asyncio.create_task(counter.flush(stuck))  # type: ignore[arg-type]
    await entered.wait()
    flush.cancel()
    await asyncio.gather(flush, return_exceptions=True)
    await counter.flush(app.state.sessionmaker)

    assert [(c.flag_key, c.variant, c.hour, c.count) for c in await counts(app)] == [
        ("color", "red", hour, 1)
    ]


async def test_retention(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    env_id = await environment_id(app)
    now = datetime.now(UTC)
    async with app.state.sessionmaker() as session:
        for flag_key, received_at in (("old", now - timedelta(days=31)), ("new", now)):
            session.add(
                ExposureEvent(
                    environment_id=env_id,
                    flag_key=flag_key,
                    reason="DEFAULT",
                    occurred_at=received_at,
                    received_at=received_at,
                )
            )
        await session.commit()

    async with app.state.engine.connect() as holder:
        await holder.execute(text("SELECT pg_advisory_lock(:id)"), {"id": RETENTION_LOCK_ID})
        async with app.state.sessionmaker() as session:
            assert await delete_expired(session, 30) == 0
            await session.commit()
        await holder.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": RETENTION_LOCK_ID})
    assert len(await exposures(app)) == 2

    async with app.state.sessionmaker() as session:
        assert await delete_expired(session, 30) == 1
        await session.commit()

    assert [e.flag_key for e in await exposures(app)] == ["new"]


def chunked(size: int, chunk: int = 64 * 1024) -> AsyncIterator[bytes]:
    """A JSON body of about `size` bytes, sent without a Content-Length."""

    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"exposures": [], "pad": "'
        for _ in range(size // chunk):
            yield b"x" * chunk
        yield b'"}'

    return chunks()


async def test_oversized_chunked_body(app: FastAPI, api: httpx.AsyncClient) -> None:
    key = await server_key(api)
    headers = {"Content-Type": "application/json"}

    r = await api.post(EVENTS, content=chunked(2 * MAX_BODY_BYTES), headers=headers)
    assert_error(r, 401, "unauthorized")

    r = await api.post(
        EVENTS, content=chunked(2 * MAX_BODY_BYTES), headers={**headers, **bearer(key)}
    )
    assert_error(r, 413, "payload_too_large")


async def test_body_limit_stops_reading() -> None:
    received = 0
    seen: dict[str, Any] = {}

    async def receive() -> dict[str, Any]:
        nonlocal received
        received += 64 * 1024
        return {"type": "http.request", "body": b"x" * 64 * 1024, "more_body": True}

    async def inner(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen["too_large"] = scope.get(BODY_TOO_LARGE)
        seen["body"] = await receive()

    limit = EventsBodyLimitMiddleware(inner)
    scope = {"type": "http", "method": "POST", "path": EVENTS, "headers": []}
    await limit(scope, receive, None)

    assert received <= MAX_BODY_BYTES + 64 * 1024
    assert seen == {
        "too_large": True,
        "body": {"type": "http.request", "body": b"{}", "more_body": False},
    }

    async def unread() -> dict[str, Any]:
        raise AssertionError("a body declared too large must not be read")

    declared = [(b"content-length", str(MAX_BODY_BYTES + 1).encode())]
    await limit({**scope, "headers": declared}, unread, None)
    assert seen["too_large"] is True

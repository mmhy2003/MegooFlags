"""Changes reach the streams of another API replica: through Valkey, through the periodic re-check
when a message is lost, and through the re-check after the subscription reconnects."""

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    count_statements,
    create_sdk_key,
    next_events,
    reset_database,
    running,
    seed_service_token,
)
from test_sdk import FLAGS, bearer, setup_demo
from test_stream import STREAM, cached, kill, principal
from valkey.asyncio import Valkey

from megooflags_api.main import create_app
from megooflags_api.services.changes import ChangeBus
from megooflags_api.services.ruleset_cache import RulesetCache
from megooflags_api.services.streams import StreamHub
from megooflags_api.settings import Settings
from megooflags_api.valkey import create_valkey

pytestmark = pytest.mark.usefixtures("migrated", "valkey_up")


@asynccontextmanager
async def replicas(
    settings_b: Settings,
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient, httpx.AsyncClient, str]]:
    """Two apps on the same Postgres and Valkey, served; "demo" is set up through A.
    Yields app A, a client for A (with an admin service token), a client for B (no
    credentials, for SDK streams) and a server key."""
    app_a = create_app(Settings())
    app_b = create_app(settings_b)
    await reset_database(app_a)
    token = await seed_service_token(app_a)
    try:
        async with (
            running(app_a) as url_a,
            running(app_b) as url_b,
            httpx.AsyncClient(base_url=url_a, timeout=10, headers=bearer(token)) as a,
            httpx.AsyncClient(base_url=url_b, timeout=10) as b,
        ):
            await setup_demo(a)
            key = (await create_sdk_key(a, "server"))["key"]
            yield app_a, a, b, key
    finally:
        for app in (app_a, app_b):
            await app.state.valkey.aclose()
            await app.state.engine.dispose()


def lose_publishes(app: FastAPI) -> None:
    """A's local dispatch still runs, but no other replica hears about its changes."""

    async def send(message: dict[str, str]) -> None:
        pass

    app.state.change_bus._send = send


@asynccontextmanager
async def stream(
    client: httpx.AsyncClient, key: str, query: bool = False
) -> AsyncIterator[AsyncIterator[str]]:
    kwargs: dict[str, object] = {"params": {"key": key}} if query else {"headers": bearer(key)}
    async with client.stream("GET", STREAM, **kwargs) as r:  # type: ignore[arg-type]
        assert r.status_code == 200, await r.aread()
        yield r.aiter_lines()


def color_enabled(data: str) -> bool:
    enabled: bool = json.loads(data)["flags"]["color"]["enabled"]
    return enabled


async def test_kill_switch_reaches_other_instance() -> None:
    async with replicas(Settings()) as (_, a, b, key), stream(b, key) as lines:
        (first,) = await next_events(lines, 1, 2)
        start = time.monotonic()
        await kill(a)
        (put,) = await next_events(lines, 1, 5)
        elapsed = time.monotonic() - start

    assert color_enabled(first.data) is True
    assert put.event == "put"
    assert color_enabled(put.data) is False
    assert elapsed < 5


async def test_lost_message_caught_by_recheck() -> None:
    async with (
        replicas(Settings(stream_recheck_seconds=0.5)) as (app_a, a, b, key),
        stream(b, key) as lines,
    ):
        await next_events(lines, 1, 2)
        lose_publishes(app_a)
        await kill(a)
        (put,) = await next_events(lines, 1, 3)

    assert put.event == "put"
    assert color_enabled(put.data) is False


async def test_resubscribe_rechecks() -> None:
    async with (
        replicas(Settings(stream_recheck_seconds=60)) as (app_a, a, b, key),
        stream(b, key) as lines,
    ):
        await next_events(lines, 1, 2)
        lose_publishes(app_a)
        await kill(a)
        admin = Valkey.from_url(app_a.state.settings.valkey_url)
        try:
            await admin.execute_command("CLIENT", "KILL", "TYPE", "pubsub")
        finally:
            await admin.aclose()
        (put,) = await next_events(lines, 1, 5)

    assert put.event == "put"
    assert color_enabled(put.data) is False


async def test_client_stream_other_instance() -> None:
    async with replicas(Settings()) as (_, a, b, _key):
        key = (await create_sdk_key(a, "client"))["key"]
        async with stream(b, key, query=True) as lines:
            (first,) = await next_events(lines, 1, 2)
            r = await a.post(
                f"{FLAGS}/color/environments/production/toggle", json={"enabled": False}
            )
            assert r.status_code == 200, r.text
            (changed,) = await next_events(lines, 1, 5)

    assert changed.event == "changed"
    assert changed.id != first.id
    assert json.loads(changed.data) == {"etag": changed.id}


async def test_idle_loops_issue_no_statements(app: FastAPI) -> None:
    app.state.settings.stream_recheck_seconds = 0.1
    async with running(app):
        with count_statements(app.state.engine) as statements:
            await asyncio.sleep(0.5)

    assert statements == []


async def test_recheck_loop_survives_errors(caplog: pytest.LogCaptureFixture) -> None:
    hub = StreamHub(None, RulesetCache())  # type: ignore[arg-type]
    calls = 0

    async def failing() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("database down")

    hub.recheck_all = failing  # type: ignore[method-assign]
    task = asyncio.create_task(hub.run_recheck_loop(0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert calls >= 2
    assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)


async def test_resubscribe_error_keeps_listening(caplog: pytest.LogCaptureFixture) -> None:
    url = os.environ["MEGOOFLAGS_VALKEY_URL"]
    client_a, client_b = create_valkey(url), create_valkey(url)
    bus_a, bus_b = ChangeBus(client_a, url), ChangeBus(client_b, url)
    subscribed, received = asyncio.Event(), asyncio.Event()

    async def failing() -> None:
        raise RuntimeError("database down")

    async def on_resubscribe() -> None:
        subscribed.set()

    bus_b.on_resubscribe += [failing, on_resubscribe]
    bus_b.on_project.append(lambda _: received.set())
    task = asyncio.create_task(bus_b.run())
    try:
        async with asyncio.timeout(2):
            await subscribed.wait()
            await bus_a.publish(uuid.uuid4())
            await received.wait()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client_a.aclose()
        await client_b.aclose()

    assert any(r.levelno == logging.ERROR and r.exc_info for r in caplog.records)


class FakeSession:
    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        pass

    async def scalar(self, statement: Any) -> uuid.UUID:
        return uuid.uuid4()  # the environment exists

    async def scalars(self, statement: Any) -> list[uuid.UUID]:
        return []  # no key is revoked or expired


async def test_recheck_and_flush_never_offer_stale() -> None:
    """A re-check that read the old ruleset must not offer it after a flush offered the new one."""
    old, new = cached('"old"'), cached('"new"')
    gate = asyncio.Event()
    reads = 0

    class SlowFirstRead(RulesetCache):
        async def get_entry(self, *args: Any, **kwargs: Any) -> Any:
            nonlocal reads
            reads += 1
            if reads == 1:
                await gate.wait()
                return old
            return new

    hub = StreamHub(FakeSession, SlowFirstRead(), debounce=0.01)  # type: ignore[arg-type]
    p = principal()
    sub = hub.subscribe(p)
    recheck = asyncio.create_task(hub.recheck_all())
    await asyncio.sleep(0.01)  # the re-check now waits inside its read of the old ruleset
    hub.notify_project(p.environment.project_id)
    await asyncio.sleep(0.05)  # the flush would read and offer the new ruleset meanwhile
    gate.set()
    await recheck
    await asyncio.sleep(0.1)  # a flush that waited for the re-check has run by now

    assert sub.queue.get_nowait() is new

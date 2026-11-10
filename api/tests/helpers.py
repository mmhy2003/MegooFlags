import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine


async def create_project(
    api: httpx.AsyncClient, key: str = "demo", environments: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"key": key, "name": key.title()}
    if environments is not None:
        body["environments"] = environments
    r = await api.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def create_flag(
    api: httpx.AsyncClient,
    project: str = "demo",
    key: str = "new-ui",
    type: str = "boolean",
    **extra: Any,
) -> dict[str, Any]:
    r = await api.post(
        f"/api/projects/{project}/flags", json={"key": key, "name": key, "type": type, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def assert_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert body["code"] == code
    return body


@contextmanager
def count_statements(engine: AsyncEngine) -> Iterator[list[str]]:
    statements: list[str] = []

    def listener(
        conn: Any, cursor: Any, statement: str, params: Any, context: Any, executemany: bool
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", listener)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)


async def create_sdk_key(
    api: httpx.AsyncClient,
    kind: str,
    project: str = "demo",
    env: str = "production",
    name: str = "k",
) -> dict[str, Any]:
    r = await api.post(
        f"/api/projects/{project}/environments/{env}/sdk-keys", json={"name": name, "kind": kind}
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[str]:
    """Serve `app` with uvicorn on a free port (lifespan on). Yields the base URL.

    httpx's ASGITransport buffers whole responses, so streaming tests need a real server.
    """
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, lifespan="on", log_config=None, timeout_graceful_shutdown=1
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    async with asyncio.timeout(10):
        while not server.started:
            if task.done():
                await task
                pytest.fail("uvicorn stopped before it started")
            await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


@dataclass
class SseEvent:
    event: str | None
    data: str
    id: str | None
    comment: bool


async def next_events(lines: AsyncIterator[str], count: int, within: float) -> list[SseEvent]:
    """The next `count` SSE events (a `: ...` comment counts as one); fails after `within` s."""
    events: list[SseEvent] = []
    fields: dict[str, list[str]] = {}

    async def read() -> None:
        async for line in lines:
            if line.startswith(":"):
                events.append(SseEvent(None, line[1:].strip(), None, comment=True))
            elif line == "":
                if fields.keys() & {"event", "data", "id"}:  # a `retry:`-only block is no event
                    events.append(
                        SseEvent(
                            event=fields["event"][-1] if "event" in fields else None,
                            data="\n".join(fields.get("data", [])),
                            id=fields["id"][-1] if "id" in fields else None,
                            comment=False,
                        )
                    )
                fields.clear()
            else:
                name, _, value = line.partition(":")
                fields.setdefault(name, []).append(value.removeprefix(" "))
            if len(events) >= count:
                return

    try:
        await asyncio.wait_for(read(), within)
    except TimeoutError:
        pytest.fail(f"expected {count} SSE events within {within} s, got {events}")
    if len(events) < count:
        pytest.fail(f"the stream ended after {events}, expected {count} events")
    return events

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
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

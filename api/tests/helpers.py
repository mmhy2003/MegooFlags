import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from valkey.exceptions import ValkeyError

from megooflags_api.models import AuditEntry, Role, RoleBinding
from megooflags_api.models.enums import ScopeKind, SubjectKind
from megooflags_api.services.api_tokens import create_service_token
from megooflags_api.services.users import create_user

# Empties every table the tests write to, keeping the built-in roles and the org_settings row.
RESET_SQL = [
    'TRUNCATE project, "user", team, role_binding, api_token, audit_entry CASCADE',
    "DELETE FROM role WHERE NOT builtin",
    "UPDATE org_settings SET require_mfa = false",
]


async def reset_database(app: FastAPI) -> None:
    async with app.state.engine.begin() as conn:
        for statement in RESET_SQL:
            await conn.execute(text(statement))
    # Throttle counters and other auth state; tests without Valkey have none to clear.
    try:
        keys = [key async for key in app.state.valkey.scan_iter(match="megooflags:auth:*")]
        if keys:
            await app.state.valkey.delete(*keys)
    except (ValkeyError, OSError, TimeoutError):
        pass


PASSWORD = "correct horse battery staple"
ORIGIN = {"Origin": "http://test"}


async def seed_user(
    app: FastAPI,
    email: str = "admin@example.com",
    *,
    password: str = PASSWORD,
    role: str = "admin",
    scope: str = "org",
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    name: str = "Test User",
) -> uuid.UUID:
    """A committed user holding `role` (a role key) at `scope`."""
    async with app.state.sessionmaker() as session:
        user = await create_user(session, email, name, password)
        await session.commit()
    await seed_binding(app, SubjectKind.USER, user.id, role, scope, project_id, environment_id)
    return user.id


async def seed_binding(
    app: FastAPI,
    subject_kind: SubjectKind | str,
    subject_id: uuid.UUID,
    role: str,
    scope: str = "org",
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """A committed binding of `role` (a role key) to the subject at `scope`."""
    async with app.state.sessionmaker() as session:
        role_id = await session.scalar(select(Role.id).where(Role.key == role))
        assert role_id is not None, f"no role {role!r}"
        binding = RoleBinding(
            subject_kind=SubjectKind(subject_kind),
            subject_id=subject_id,
            role_id=role_id,
            scope=ScopeKind(scope),
            project_id=project_id,
            environment_id=environment_id,
        )
        session.add(binding)
        await session.commit()
        return binding.id


async def seed_service_token(
    app: FastAPI,
    role: str = "admin",
    *,
    scope: str = "org",
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    name: str = "tests",
) -> str:
    """A committed service token holding `role` at `scope`. Returns the secret.

    `last_used_at` is set, so requests made with it do not write it again.
    """
    async with app.state.sessionmaker() as session:
        row, token = await create_service_token(session, name, created_by=None, expires_at=None)
        row.last_used_at = await session.scalar(select(func.now()))
        await session.commit()
    await seed_binding(
        app, SubjectKind.SERVICE_TOKEN, row.id, role, scope, project_id, environment_id
    )
    return token


async def sign_in(
    client: httpx.AsyncClient, email: str = "admin@example.com", password: str = PASSWORD
) -> httpx.Response:
    return await client.post(
        "/api/auth/sign-in", json={"email": email, "password": password}, headers=ORIGIN
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def audit_entries(app: FastAPI, action: str | None = None) -> list[dict[str, Any]]:
    """The audit entries in insertion order, optionally only those with `action`."""
    statement = select(AuditEntry.__table__).order_by(AuditEntry.id)
    if action is not None:
        statement = statement.where(AuditEntry.action == action)
    async with app.state.sessionmaker() as session:
        rows = await session.execute(statement)
        return [dict(row) for row in rows.mappings()]


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

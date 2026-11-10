import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from helpers import count_statements, seed_user
from sqlalchemy import func, select, update
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from megooflags_api.auth.sessions import (
    COOKIE_PLAIN,
    COOKIE_SECURE,
    clear_cookie,
    create_session,
    end_sessions,
    lookup_session,
    read_cookie,
    rotate,
    set_cookie,
    touch,
)
from megooflags_api.main import create_app
from megooflags_api.models import User, UserSession
from megooflags_api.models.enums import SessionState
from megooflags_api.settings import Settings

# Same value as FORWARDED_ALLOW_IPS in api/Dockerfile and deploy/docker-compose.yml.
PRIVATE_RANGES = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1"


def make_request(
    app: Any, *, scheme: str = "http", headers: dict[str, str] | None = None
) -> Request:
    raw = {"host": "test", "user-agent": "pytest-agent", **(headers or {})}
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": scheme,
            "path": "/",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in raw.items()],
            "server": ("test", 443 if scheme == "https" else 80),
            "client": ("192.0.2.10", 5555),
            "app": app,
        }
    )


async def new_session(
    app: FastAPI, state: SessionState = SessionState.ACTIVE, auth_method: str = "password"
) -> tuple[UserSession, str, uuid.UUID]:
    user_id = await seed_user(app)
    async with app.state.sessionmaker() as session:
        user = await session.get_one(User, user_id)
        row, token = await create_session(
            session,
            make_request(app),
            user,
            state=state,
            auth_method=auth_method,
            settings=app.state.settings,
        )
        await session.commit()
    return row, token, user_id


async def shift(app: FastAPI, session_id: uuid.UUID, **values: datetime) -> None:
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(UserSession).where(UserSession.id == session_id).values(**values)
        )
        await session.commit()


async def session_count(app: FastAPI) -> int:
    async with app.state.sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(UserSession)) or 0


async def test_only_the_token_hash_is_stored(app: FastAPI) -> None:
    row, token, user_id = await new_session(app)

    assert len(token) >= 43
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.user_id == user_id
    assert row.state == SessionState.ACTIVE
    assert row.auth_method == "password"
    assert row.ip == "192.0.2.10"
    assert row.user_agent == "pytest-agent"
    assert row.mfa_verified_at is None
    async with app.state.sessionmaker() as session:
        stored = await session.scalar(select(func.count()).where(UserSession.token_hash == token))
    assert stored == 0


async def test_lookup_finds_the_session_and_user(app: FastAPI) -> None:
    row, token, user_id = await new_session(app)

    async with app.state.sessionmaker() as session:
        found = await lookup_session(session, token)
        assert found is not None
        assert found[0].id == row.id
        assert found[1].id == user_id
        assert await lookup_session(session, token + "x") is None


async def test_expiry_lengths_follow_the_settings(app: FastAPI) -> None:
    row, _, _ = await new_session(app)

    assert row.idle_expires_at - row.created_at == timedelta(hours=12)
    assert row.absolute_expires_at - row.created_at == timedelta(days=7)


async def test_idle_expired_session_is_deleted(app: FastAPI) -> None:
    row, token, _ = await new_session(app)
    await shift(app, row.id, idle_expires_at=datetime.now(UTC) - timedelta(seconds=1))

    async with app.state.sessionmaker() as session:
        assert await lookup_session(session, token) is None
    assert await session_count(app) == 0


async def test_absolutely_expired_session_is_deleted(app: FastAPI) -> None:
    row, token, _ = await new_session(app)
    await shift(app, row.id, absolute_expires_at=datetime.now(UTC) - timedelta(seconds=1))

    async with app.state.sessionmaker() as session:
        assert await lookup_session(session, token) is None
    assert await session_count(app) == 0


async def test_pending_session_expires_after_pending_mfa_minutes(app: FastAPI) -> None:
    row, token, _ = await new_session(app, SessionState.PENDING_MFA)

    assert row.idle_expires_at - row.created_at == timedelta(minutes=5)
    assert row.absolute_expires_at - row.created_at == timedelta(minutes=5)

    await shift(
        app,
        row.id,
        idle_expires_at=row.idle_expires_at - timedelta(minutes=5, seconds=1),
        absolute_expires_at=row.absolute_expires_at - timedelta(minutes=5, seconds=1),
    )
    async with app.state.sessionmaker() as session:
        assert await lookup_session(session, token) is None
    assert await session_count(app) == 0


async def test_touch_writes_at_most_every_60_seconds(app: FastAPI) -> None:
    settings = app.state.settings
    row, token, _ = await new_session(app)

    async with app.state.sessionmaker() as session:
        found = await lookup_session(session, token)
        assert found is not None
        with count_statements(app.state.engine) as statements:
            await touch(session, found[0], settings)
        assert statements == []

    seen = datetime.now(UTC) - timedelta(seconds=61)
    await shift(app, row.id, last_seen_at=seen, idle_expires_at=seen + timedelta(hours=1))
    async with app.state.sessionmaker() as session:
        found = await lookup_session(session, token)
        assert found is not None
        before = datetime.now(UTC)
        await touch(session, found[0], settings)
    async with app.state.sessionmaker() as session:
        stored = await session.get_one(UserSession, row.id)
    assert stored.last_seen_at >= before
    assert stored.idle_expires_at >= before + timedelta(hours=12)

    capped = datetime.now(UTC) + timedelta(hours=1)
    await shift(app, row.id, last_seen_at=seen, absolute_expires_at=capped)
    async with app.state.sessionmaker() as session:
        found = await lookup_session(session, token)
        assert found is not None
        await touch(session, found[0], settings)
    async with app.state.sessionmaker() as session:
        stored = await session.get_one(UserSession, row.id)
    assert stored.idle_expires_at == capped


async def test_rotate_issues_a_new_token(app: FastAPI) -> None:
    row, token, user_id = await new_session(app, SessionState.PENDING_MFA)

    async with app.state.sessionmaker() as session:
        found = await lookup_session(session, token)
        assert found is not None
        new_row, new_token = await rotate(
            session,
            make_request(app),
            found[0],
            state=SessionState.ACTIVE,
            settings=app.state.settings,
            mfa_verified=True,
        )
        await session.commit()

    assert new_token != token
    assert new_row.id != row.id
    assert new_row.user_id == user_id
    assert new_row.auth_method == "password"
    assert new_row.state == SessionState.ACTIVE
    assert new_row.mfa_verified_at is not None
    assert new_row.absolute_expires_at - new_row.created_at == timedelta(days=7)
    async with app.state.sessionmaker() as session:
        assert await lookup_session(session, token) is None
        found = await lookup_session(session, new_token)
        assert found is not None
        assert found[0].id == new_row.id
    assert await session_count(app) == 1


async def test_end_sessions_keeps_only_the_excepted_one(app: FastAPI) -> None:
    user_id = await seed_user(app)
    other_id = await seed_user(app, "other@example.com")
    async with app.state.sessionmaker() as session:
        user = await session.get_one(User, user_id)
        other = await session.get_one(User, other_id)
        request = make_request(app)
        kwargs: dict[str, Any] = {
            "state": SessionState.ACTIVE,
            "auth_method": "password",
            "settings": app.state.settings,
        }
        rows = [(await create_session(session, request, user, **kwargs))[0] for _ in range(3)]
        await create_session(session, request, other, **kwargs)
        await session.commit()

        assert await end_sessions(session, user_id, except_id=rows[1].id) == 2
        await session.commit()

        remaining = set(await session.scalars(select(UserSession.id)))
    assert len(remaining) == 2
    assert rows[1].id in remaining
    assert rows[0].id not in remaining
    assert rows[2].id not in remaining


def set_cookie_header(scheme: str) -> str:
    response = Response()
    set_cookie(response, make_request(None, scheme=scheme), "tok", 3600)
    return response.headers["set-cookie"]


def test_cookie_over_http() -> None:
    header = set_cookie_header("http")

    assert header.startswith(f"{COOKIE_PLAIN}=tok;")
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header
    assert "Max-Age=3600" in header
    assert "Secure" not in header


def test_cookie_over_https() -> None:
    header = set_cookie_header("https")

    assert header.startswith(f"{COOKIE_SECURE}=tok;")
    assert "Secure" in header
    assert "HttpOnly" in header
    assert "SameSite=lax" in header
    assert "Path=/" in header


def test_read_cookie_uses_the_name_for_the_scheme() -> None:
    cookies = f"{COOKIE_PLAIN}=plain; {COOKIE_SECURE}=secure"
    assert read_cookie(make_request(None, headers={"cookie": cookies})) == "plain"
    assert read_cookie(make_request(None, scheme="https", headers={"cookie": cookies})) == "secure"
    assert read_cookie(make_request(None)) is None


def test_clear_cookie_expires_it() -> None:
    response = Response()
    clear_cookie(response, make_request(None, scheme="https"))

    header = response.headers["set-cookie"]
    assert header.startswith(f'{COOKIE_SECURE}="";')
    assert "Max-Age=0" in header
    assert "Secure" in header


async def proxied_cookie(client_ip: str) -> str:
    app = create_app(Settings())

    async def endpoint(request: Request) -> Response:
        response = Response()
        set_cookie(response, request, "tok", 60)
        return response

    app.add_api_route("/_cookie", endpoint)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=PRIVATE_RANGES)
    transport = httpx.ASGITransport(app=wrapped, client=(client_ip, 4321))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/_cookie", headers={"X-Forwarded-Proto": "https"})
            return response.headers["set-cookie"]
    finally:
        await app.state.valkey.aclose()
        await app.state.engine.dispose()


async def test_forwarded_https_from_trusted_proxy_sets_the_host_cookie() -> None:
    header = await proxied_cookie("172.18.0.5")
    assert header.startswith(f"{COOKIE_SECURE}=tok;")
    assert "Secure" in header


async def test_forwarded_https_from_untrusted_ip_sets_the_plain_cookie() -> None:
    header = await proxied_cookie("203.0.113.9")
    assert header.startswith(f"{COOKIE_PLAIN}=tok;")
    assert "Secure" not in header

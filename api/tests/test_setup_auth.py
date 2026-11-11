import asyncio
import logging
import uuid

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    PASSWORD,
    assert_error,
    audit_entries,
    bearer,
    seed_service_token,
    seed_user,
    sign_in,
)
from sqlalchemy import func, select, update

from megooflags_api.auth.sessions import COOKIE_PLAIN
from megooflags_api.auth.throttle import AuthThrottle
from megooflags_api.models import Role, RoleBinding, User, UserSession
from megooflags_api.models.enums import ScopeKind, SubjectKind, UserStatus
from megooflags_api.valkey import create_valkey

SETUP = {"email": "first@example.com", "name": "First Admin", "password": PASSWORD}
NO_USER = "no user exists"


async def session_count(app: FastAPI) -> int:
    async with app.state.sessionmaker() as session:
        return int(await session.scalar(select(func.count()).select_from(UserSession)) or 0)


async def get_user(app: FastAPI, email: str) -> User:
    async with app.state.sessionmaker() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user is not None
        return user


# Setup


async def test_setup_status_starts_true(anon: httpx.AsyncClient) -> None:
    r = await anon.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json() == {"needs_setup": True}


async def test_setup_creates_an_admin_and_signs_in(app: FastAPI, anon: httpx.AsyncClient) -> None:
    r = await anon.post("/api/setup", json=SETUP)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "signed_in"
    assert COOKIE_PLAIN in r.cookies

    user = await get_user(app, "first@example.com")
    async with app.state.sessionmaker() as session:
        bindings = (
            await session.execute(
                select(RoleBinding.scope, Role.key)
                .join(Role, Role.id == RoleBinding.role_id)
                .where(
                    RoleBinding.subject_kind == SubjectKind.USER,
                    RoleBinding.subject_id == user.id,
                )
            )
        ).all()
    assert bindings == [(ScopeKind.ORG, "admin")]

    [entry] = await audit_entries(app, "auth.setup")
    assert entry["actor_id"] == user.id
    assert entry["actor_label"] == "first@example.com"

    me = await anon.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "first@example.com"
    assert (await anon.get("/api/setup/status")).json() == {"needs_setup": False}


async def test_second_setup_is_409(anon: httpx.AsyncClient) -> None:
    assert (await anon.post("/api/setup", json=SETUP)).status_code == 201
    again = {**SETUP, "email": "second@example.com"}
    assert_error(await anon.post("/api/setup", json=again), 409, "already_set_up")


async def test_concurrent_setup_has_one_winner(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as a,
        httpx.AsyncClient(transport=transport, base_url="http://test") as b,
    ):
        results = await asyncio.gather(
            a.post("/api/setup", json=SETUP),
            b.post("/api/setup", json={**SETUP, "email": "other@example.com"}),
        )
    assert sorted(r.status_code for r in results) == [201, 409]
    async with app.state.sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_setup_with_weak_password_is_422(anon: httpx.AsyncClient) -> None:
    r = await anon.post("/api/setup", json={**SETUP, "password": "short"})
    assert_error(r, 422, "weak_password")


# Sign-in


async def test_sign_in(app: FastAPI, anon: httpx.AsyncClient) -> None:
    user_id = await seed_user(app)
    r = await sign_in(anon)
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "signed_in", "mfa_methods": []}
    assert COOKIE_PLAIN in r.cookies

    [entry] = await audit_entries(app, "auth.sign_in")
    assert entry["actor_id"] == user_id
    assert entry["auth_method"] == "password"
    async with app.state.sessionmaker() as session:
        user = await session.get_one(User, user_id)
        assert user.last_login_at is not None


@pytest.mark.parametrize("case", ["wrong_password", "unknown_email", "disabled"])
async def test_sign_in_failures_look_the_same(
    app: FastAPI, anon: httpx.AsyncClient, case: str
) -> None:
    user_id = await seed_user(app)
    email, password = "admin@example.com", PASSWORD
    if case == "wrong_password":
        password = "not the right password"
    elif case == "unknown_email":
        email = "nobody@example.com"
    else:
        async with app.state.sessionmaker() as session:
            await session.execute(
                update(User).where(User.id == user_id).values(status=UserStatus.DISABLED)
            )
            await session.commit()

    r = await sign_in(anon, email, password)
    body = assert_error(r, 401, "invalid_credentials")
    assert body["message"] == "invalid email or password"
    assert COOKIE_PLAIN not in r.cookies
    [entry] = await audit_entries(app, "auth.sign_in.failed")
    assert entry["actor_kind"] == "anonymous"
    assert entry["actor_label"] == email
    assert await session_count(app) == 0


async def test_sign_in_is_throttled_per_account(
    valkey_up: None, app: FastAPI, anon: httpx.AsyncClient
) -> None:
    await seed_user(app)
    for _ in range(10):
        assert (await sign_in(anon, password="wrong password!!")).status_code == 401
    r = await sign_in(anon)
    assert_error(r, 429, "rate_limited")
    assert 0 < int(r.headers["Retry-After"]) <= 900


async def test_successful_sign_in_clears_the_account_counter(
    valkey_up: None, app: FastAPI, anon: httpx.AsyncClient
) -> None:
    await seed_user(app)
    for _ in range(9):
        assert (await sign_in(anon, password="wrong password!!")).status_code == 401
    assert (await sign_in(anon)).status_code == 200
    for _ in range(9):
        assert (await sign_in(anon, password="wrong password!!")).status_code == 401
    assert (await sign_in(anon)).status_code == 200


async def test_sign_in_replaces_an_existing_session(app: FastAPI, anon: httpx.AsyncClient) -> None:
    await seed_user(app)
    first = (await sign_in(anon)).cookies[COOKIE_PLAIN]
    second = (await sign_in(anon)).cookies[COOKIE_PLAIN]
    assert first != second
    assert await session_count(app) == 1
    assert (await anon.get("/api/auth/me")).status_code == 200
    anon.cookies.clear()
    anon.cookies.set(COOKIE_PLAIN, first)
    assert_error(await anon.get("/api/auth/me"), 401, "unauthorized")


# Throttle


async def test_throttle_limits_per_ip(valkey_up: None, app: FastAPI) -> None:
    throttle = AuthThrottle(app.state.valkey)
    for i in range(100):
        await throttle.failed(f"user{i}@example.com", "192.0.2.1")
    assert await throttle.blocked("fresh@example.com", "192.0.2.1") is not None
    assert await throttle.blocked("fresh@example.com", "192.0.2.2") is None


async def test_throttle_limits_mfa_per_user(valkey_up: None, app: FastAPI) -> None:
    throttle = AuthThrottle(app.state.valkey)
    user_id = uuid.uuid4()
    for _ in range(9):
        await throttle.mfa_failed(user_id)
    assert await throttle.mfa_blocked(user_id) is None
    await throttle.mfa_failed(user_id)
    assert await throttle.mfa_blocked(user_id) is not None


async def test_throttle_fails_open() -> None:
    client = create_valkey("redis://127.0.0.1:1/0")
    throttle = AuthThrottle(client)
    try:
        await throttle.failed("a@example.com", "192.0.2.1")
        await throttle.cleared("a@example.com")
        await throttle.mfa_failed(uuid.uuid4())
        assert await throttle.blocked("a@example.com", "192.0.2.1") is None
        assert await throttle.mfa_blocked(uuid.uuid4()) is None
    finally:
        await client.aclose()


# /auth/me and sign-out


async def test_me(app: FastAPI, anon: httpx.AsyncClient) -> None:
    user_id = await seed_user(app, name="Ada")
    assert_error(await anon.get("/api/auth/me"), 401, "unauthorized")
    await sign_in(anon)
    r = await anon.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() == {
        "kind": "user",
        "id": str(user_id),
        "label": "admin@example.com",
        "email": "admin@example.com",
        "name": "Ada",
        "auth_method": "password",
        "session_state": "active",
        "mfa": {"totp": False, "passkeys": 0, "recovery_codes_left": 0},
        "require_mfa": False,
    }


async def test_me_with_a_service_token(app: FastAPI, anon: httpx.AsyncClient) -> None:
    token = await seed_service_token(app, name="ci")
    r = await anon.get("/api/auth/me", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert (body["kind"], body["label"], body["email"], body["mfa"]) == (
        "service_token",
        "ci",
        None,
        None,
    )


async def test_sign_out(app: FastAPI, anon: httpx.AsyncClient) -> None:
    user_id = await seed_user(app)
    await sign_in(anon)
    r = await anon.post("/api/auth/sign-out")
    assert r.status_code == 204
    assert await session_count(app) == 0
    set_cookie = r.headers["set-cookie"]
    assert set_cookie.startswith(f"{COOKIE_PLAIN}=") and "Max-Age=0" in set_cookie
    [entry] = await audit_entries(app, "auth.sign_out")
    assert entry["actor_id"] == user_id
    assert_error(await anon.get("/api/auth/me"), 401, "unauthorized")


async def test_sign_out_needs_a_session(app: FastAPI, anon: httpx.AsyncClient) -> None:
    assert_error(await anon.post("/api/auth/sign-out"), 401, "unauthorized")
    token = await seed_service_token(app)
    r = await anon.post("/api/auth/sign-out", headers=bearer(token))
    assert_error(r, 403, "forbidden")


async def test_sign_out_checks_the_origin(app: FastAPI, anon: httpx.AsyncClient) -> None:
    await seed_user(app)
    await sign_in(anon)
    r = await anon.post("/api/auth/sign-out", headers={"Origin": "http://evil.example"})
    assert_error(r, 403, "csrf_failed")
    assert await session_count(app) == 1


# The no-user warning


async def test_readyz_warns_until_setup(
    valkey_up: None, app: FastAPI, anon: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    assert (await anon.get("/readyz")).status_code == 200
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(NO_USER in r.getMessage() for r in warnings)

    caplog.clear()
    assert (await anon.post("/api/setup", json=SETUP)).status_code == 201
    assert (await anon.get("/readyz")).status_code == 200
    assert not any(NO_USER in r.getMessage() for r in caplog.records)


async def test_startup_warns_without_users(
    valkey_up: None, app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    async with app.router.lifespan_context(app):
        pass
    assert any(NO_USER in r.getMessage() for r in caplog.records)

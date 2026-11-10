"""The signed-in user's own account: password change and sessions (`/account/*`)."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from helpers import (
    ORIGIN,
    PASSWORD,
    assert_error,
    audit_entries,
    bearer,
    seed_service_token,
    seed_user,
    sign_in,
)
from sqlalchemy import select, update

from megooflags_api.models import User, UserSession

NEW_PASSWORD = "a much longer passphrase 42"
COOKIE = "mf_session"


@asynccontextmanager
async def signed_in_client(
    app: FastAPI, email: str = "admin@example.com", password: str = PASSWORD
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=ORIGIN
    ) as client:
        r = await sign_in(client, email, password)
        assert r.status_code == 200, r.text
        yield client


@asynccontextmanager
async def cookie_client(app: FastAPI, token: str) -> AsyncIterator[httpx.AsyncClient]:
    """A client that sends `token` as its session cookie."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=ORIGIN, cookies={COOKIE: token}
    ) as client:
        yield client


async def change_password(
    client: httpx.AsyncClient, current: str = PASSWORD, new: str = NEW_PASSWORD
) -> httpx.Response:
    return await client.put(
        "/api/account/password", json={"current_password": current, "new_password": new}
    )


async def session_ids(app: FastAPI, user_id: uuid.UUID) -> set[uuid.UUID]:
    async with app.state.sessionmaker() as session:
        rows = await session.scalars(select(UserSession.id).where(UserSession.user_id == user_id))
        return set(rows)


# Password


async def test_wrong_current_password_is_refused(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        r = await change_password(client, current="not the right password")

    assert_error(r, 401, "invalid_credentials")
    assert await audit_entries(app, "account.password.change") == []


async def test_weak_new_password_is_refused(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        r = await change_password(client, new="short")

    assert_error(r, 422, "weak_password")


async def test_change_rotates_the_session_and_ends_the_others(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as other, signed_in_client(app) as client:
        old_token = client.cookies[COOKIE]

        r = await change_password(client)

        assert r.status_code == 204, r.text
        new_token = r.cookies[COOKIE]
        assert new_token != old_token
        assert (await client.get("/api/auth/me")).status_code == 200
        assert (await other.get("/api/auth/me")).status_code == 401
    async with cookie_client(app, old_token) as stale:
        assert (await stale.get("/api/auth/me")).status_code == 401

    async with signed_in_client(app, password=NEW_PASSWORD):
        pass
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as anon:
        assert_error(await sign_in(anon), 401, "invalid_credentials")

    [entry] = await audit_entries(app, "account.password.change")
    assert entry["actor_kind"] == "user"
    assert entry["resource_key"] == "admin@example.com"
    assert PASSWORD not in str(entry)
    assert NEW_PASSWORD not in str(entry)


async def test_user_without_a_password_gets_no_password(app: FastAPI) -> None:
    user_id = await seed_user(app)
    async with signed_in_client(app) as client:
        async with app.state.sessionmaker() as session:
            await session.execute(update(User).where(User.id == user_id).values(password_hash=None))
            await session.commit()

        r = await change_password(client)

    assert_error(r, 409, "no_password")


async def test_change_without_origin_fails_the_csrf_check(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        del client.headers["Origin"]
        r = await change_password(client)

    assert_error(r, 403, "csrf_failed")


# Sessions


async def test_list_shows_the_sessions_and_marks_the_current_one(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app), signed_in_client(app) as client:
        r = await client.get("/api/account/sessions")

    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 2
    assert [item["current"] for item in items].count(True) == 1
    assert {item["auth_method"] for item in items} == {"password"}
    assert set(items[0]) == {
        "id",
        "created_at",
        "last_seen_at",
        "ip",
        "user_agent",
        "auth_method",
        "current",
    }


async def test_list_shows_only_your_own_sessions(app: FastAPI) -> None:
    await seed_user(app)
    await seed_user(app, "other@example.com")
    async with signed_in_client(app, "other@example.com"), signed_in_client(app) as client:
        r = await client.get("/api/account/sessions")

    assert r.status_code == 200, r.text
    assert [item["current"] for item in r.json()] == [True]


async def test_revoke_one_of_your_other_sessions(app: FastAPI) -> None:
    user_id = await seed_user(app)
    async with signed_in_client(app) as other, signed_in_client(app) as client:
        items = (await client.get("/api/account/sessions")).json()
        [other_id] = [item["id"] for item in items if not item["current"]]

        r = await client.delete(f"/api/account/sessions/{other_id}")

        assert r.status_code == 204, r.text
        assert (await other.get("/api/auth/me")).status_code == 401
        assert (await client.get("/api/auth/me")).status_code == 200
    assert len(await session_ids(app, user_id)) == 1
    [entry] = await audit_entries(app, "account.session.revoke")
    assert entry["resource_key"] == other_id


async def test_revoking_another_users_session_is_not_found(app: FastAPI) -> None:
    await seed_user(app)
    other_user = await seed_user(app, "other@example.com")
    async with signed_in_client(app, "other@example.com"), signed_in_client(app) as client:
        [other_id] = await session_ids(app, other_user)

        r = await client.delete(f"/api/account/sessions/{other_id}")

    assert_error(r, 404, "not_found")
    assert await session_ids(app, other_user) == {other_id}


async def test_revoking_the_current_session_signs_out(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        [item] = (await client.get("/api/account/sessions")).json()

        r = await client.delete(f"/api/account/sessions/{item['id']}")

        assert r.status_code == 204, r.text
        assert COOKIE not in client.cookies
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_revoke_others_leaves_only_the_current_session(app: FastAPI) -> None:
    user_id = await seed_user(app)
    async with (
        signed_in_client(app) as first,
        signed_in_client(app) as second,
        signed_in_client(app) as client,
    ):
        r = await client.delete("/api/account/sessions/others")

        assert r.status_code == 204, r.text
        assert (await first.get("/api/auth/me")).status_code == 401
        assert (await second.get("/api/auth/me")).status_code == 401
        [item] = (await client.get("/api/account/sessions")).json()
        assert item["current"] is True
    assert await session_ids(app, user_id) == {uuid.UUID(item["id"])}
    entries = await audit_entries(app, "account.session.revoke")
    assert len(entries) == 2


async def test_tokens_cannot_use_the_account_endpoints(app: FastAPI) -> None:
    token = await seed_service_token(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=bearer(token)
    ) as client:
        r = await client.get("/api/account/sessions")
        assert_error(r, 403, "forbidden")
        r = await client.delete("/api/account/sessions/others")
        assert_error(r, 403, "forbidden")
        r = await change_password(client)
        assert_error(r, 403, "forbidden")


async def test_account_needs_credentials(app: FastAPI, anon: httpx.AsyncClient) -> None:
    assert (await anon.get("/api/account/sessions")).status_code == 401

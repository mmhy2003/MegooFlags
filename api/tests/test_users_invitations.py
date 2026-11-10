import hashlib
import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
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

from megooflags_api.models import Invitation, UserSession
from megooflags_api.models.enums import InvitationPurpose
from megooflags_api.tracing import EXCLUDED_URLS

NEW_PASSWORD = "a brand new passphrase for tests"
INVITE_URL = re.compile(r"^http://test/invite/([A-Za-z0-9_-]{43})$")


@pytest.fixture
async def admin(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    token = await seed_service_token(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=bearer(token)
    ) as client:
        yield client


async def create(
    admin: httpx.AsyncClient, email: str = "new@example.com", name: str = "New User"
) -> tuple[dict[str, Any], str]:
    """Creates a user; returns the user and the invitation token."""
    r = await admin.post("/api/users", json={"email": email, "name": name})
    assert r.status_code == 201, r.text
    body = r.json()
    match = INVITE_URL.match(body["invite"]["url"])
    assert match, body["invite"]["url"]
    return body["user"], match.group(1)


async def accept(
    anon: httpx.AsyncClient, token: str, password: str = NEW_PASSWORD
) -> httpx.Response:
    return await anon.post(f"/api/invitations/{token}", json={"password": password})


async def invitations(app: FastAPI) -> list[Invitation]:
    async with app.state.sessionmaker() as session:
        return list(await session.scalars(select(Invitation).order_by(Invitation.created_at)))


async def session_count(app: FastAPI, user_id: uuid.UUID) -> int:
    async with app.state.sessionmaker() as session:
        rows = await session.scalars(select(UserSession.id).where(UserSession.user_id == user_id))
        return len(list(rows))


@asynccontextmanager
async def signed_in_client(app: FastAPI, email: str) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=ORIGIN
    ) as client:
        r = await sign_in(client, email)
        assert r.status_code == 200, r.text
        yield client


# Creating users and accepting invitations


async def test_create_returns_an_invite_link_and_stores_only_its_hash(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    r = await admin.post("/api/users", json={"email": "New@Example.com", "name": "New User"})
    assert r.status_code == 201, r.text
    body = r.json()
    user = body["user"]
    assert (user["email"], user["name"], user["status"]) == (
        "New@Example.com",
        "New User",
        "active",
    )
    assert user["has_password"] is False
    match = INVITE_URL.match(body["invite"]["url"])
    assert match, body["invite"]["url"]
    token = match.group(1)
    expires_at = datetime.fromisoformat(body["invite"]["expires_at"])
    assert timedelta(days=6, hours=23) < expires_at - datetime.now(UTC) <= timedelta(days=7)

    [row] = await invitations(app)
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert row.purpose == InvitationPurpose.INVITE
    assert row.used_at is None
    assert str(row.user_id) == user["id"]

    [entry] = await audit_entries(app, "user.create")
    assert (entry["resource_type"], entry["resource_key"]) == ("user", "New@Example.com")
    assert entry["before"] is None
    assert entry["after"] == user
    assert token not in str(entry)


async def test_create_refuses_a_duplicate_email(admin: httpx.AsyncClient) -> None:
    await create(admin, "dup@example.com")
    r = await admin.post("/api/users", json={"email": "DUP@example.com", "name": "Again"})
    assert_error(r, 409, "key_conflict")


async def test_accepting_an_invitation_sets_the_password(
    app: FastAPI, admin: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    user, token = await create(admin)

    r = await anon.get(f"/api/invitations/{token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["email"], body["name"], body["purpose"]) == (
        "new@example.com",
        "New User",
        "invite",
    )
    assert body["expires_at"]

    assert_error(await accept(anon, token, "short"), 422, "weak_password")
    # A refused password does not use up the link.
    assert (await anon.get(f"/api/invitations/{token}")).status_code == 200

    r = await accept(anon, token)
    assert r.status_code == 204, r.text
    assert (await sign_in(anon, "new@example.com", NEW_PASSWORD)).status_code == 200

    [entry] = await audit_entries(app, "invitation.accept")
    assert entry["actor_kind"] == "anonymous"
    assert entry["actor_label"] == "new@example.com"
    assert (entry["resource_type"], entry["resource_key"]) == ("user", "new@example.com")
    assert entry["after"]["has_password"] is True
    assert entry["after"]["id"] == user["id"]


async def test_invalid_invitations_all_look_the_same(
    app: FastAPI, admin: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    user, used = await create(admin, "used@example.com")
    assert (await accept(anon, used)).status_code == 204

    _, expired = await create(admin, "expired@example.com")
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(Invitation)
            .where(Invitation.token_hash == hashlib.sha256(expired.encode()).hexdigest())
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()

    sup_user, superseded = await create(admin, "superseded@example.com")
    r = await admin.post(f"/api/users/{sup_user['id']}/reset-link")
    assert r.status_code == 200, r.text

    unknown = "x" * 43
    errors = []
    for token in (used, expired, superseded, unknown):
        get = await anon.get(f"/api/invitations/{token}")
        post = await accept(anon, token)
        errors += [
            assert_error(get, 404, "invalid_invitation"),
            assert_error(post, 404, "invalid_invitation"),
        ]
    assert all(e == errors[0] for e in errors)


async def test_reset_link_replaces_older_links_and_ends_sessions(
    app: FastAPI, admin: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    await seed_user(app, "admin@example.com")
    user_id = await seed_user(app, "member@example.com", role="viewer")
    async with signed_in_client(app, "member@example.com") as member:
        first = await admin.post(f"/api/users/{user_id}/reset-link")
        assert first.status_code == 200, first.text
        second = await admin.post(f"/api/users/{user_id}/reset-link")
        assert second.status_code == 200, second.text
        assert set(second.json()) == {"url", "expires_at"}
        old_match = INVITE_URL.match(first.json()["url"])
        new_match = INVITE_URL.match(second.json()["url"])
        assert old_match and new_match
        old, new = old_match.group(1), new_match.group(1)

        assert_error(await anon.get(f"/api/invitations/{old}"), 404, "invalid_invitation")
        r = await anon.get(f"/api/invitations/{new}")
        assert r.status_code == 200, r.text
        assert r.json()["purpose"] == "reset"

        assert await session_count(app, user_id) == 1
        assert (await accept(anon, new)).status_code == 204
        assert await session_count(app, user_id) == 0
        assert_error(await member.get("/api/auth/me"), 401, "unauthorized")

    assert (await sign_in(anon, "member@example.com", PASSWORD)).status_code == 401
    assert (await sign_in(anon, "member@example.com", NEW_PASSWORD)).status_code == 200
    resets = await audit_entries(app, "user.reset_link")
    assert [e["resource_key"] for e in resets] == ["member@example.com"] * 2
    assert new not in str(await audit_entries(app))


# Managing users


async def test_disable_ends_sessions_and_blocks_sign_in_until_enabled(
    app: FastAPI, admin: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    await seed_user(app, "admin@example.com")
    user_id = await seed_user(app, "member@example.com", role="viewer")
    async with signed_in_client(app, "member@example.com") as member:
        r = await admin.post(f"/api/users/{user_id}/disable")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "disabled"
        assert await session_count(app, user_id) == 0
        assert_error(await member.get("/api/auth/me"), 401, "unauthorized")
    assert_error(await sign_in(anon, "member@example.com"), 401, "invalid_credentials")

    r = await admin.post(f"/api/users/{user_id}/enable")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    assert (await sign_in(anon, "member@example.com")).status_code == 200

    [disable] = await audit_entries(app, "user.disable")
    assert (disable["before"]["status"], disable["after"]["status"]) == ("active", "disabled")
    [enable] = await audit_entries(app, "user.enable")
    assert (enable["before"]["status"], enable["after"]["status"]) == ("disabled", "active")


async def test_disabling_the_only_admin_is_refused(app: FastAPI, admin: httpx.AsyncClient) -> None:
    admin_id = await seed_user(app, "admin@example.com")
    assert_error(await admin.post(f"/api/users/{admin_id}/disable"), 409, "last_admin")
    r = await admin.get(f"/api/users/{admin_id}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"


async def test_get_and_patch(app: FastAPI, admin: httpx.AsyncClient) -> None:
    user, _ = await create(admin)
    r = await admin.get(f"/api/users/{user['id']}")
    assert r.status_code == 200, r.text
    assert r.json() == user

    r = await admin.patch(f"/api/users/{user['id']}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json() == {**user, "name": "Renamed"}
    [entry] = await audit_entries(app, "user.update")
    assert entry["before"] == user
    assert entry["after"]["name"] == "Renamed"

    assert_error(await admin.get(f"/api/users/{uuid.uuid4()}"), 404, "not_found")


async def test_list_pages_by_email_and_filters_by_q(admin: httpx.AsyncClient) -> None:
    for email, name in [
        ("carol@example.com", "Carol"),
        ("Alice@example.com", "Alice"),
        ("bob@example.com", "Bob Builder"),
    ]:
        await create(admin, email, name)

    r = await admin.get("/api/users", params={"limit": 2})
    assert r.status_code == 200, r.text
    first = r.json()
    assert [u["email"] for u in first["items"]] == ["Alice@example.com", "bob@example.com"]
    assert first["next_cursor"]
    r = await admin.get("/api/users", params={"limit": 2, "cursor": first["next_cursor"]})
    second = r.json()
    assert [u["email"] for u in second["items"]] == ["carol@example.com"]
    assert second["next_cursor"] is None

    r = await admin.get("/api/users", params={"q": "BUILD"})
    assert [u["email"] for u in r.json()["items"]] == ["bob@example.com"]
    r = await admin.get("/api/users", params={"q": "ALICE@"})
    assert [u["email"] for u in r.json()["items"]] == ["Alice@example.com"]
    r = await admin.get("/api/users", params={"q": "%"})
    assert r.json()["items"] == []


async def test_viewer_is_forbidden_everywhere(app: FastAPI, admin: httpx.AsyncClient) -> None:
    user, _ = await create(admin)
    await seed_user(app, "viewer@example.com", role="viewer")
    uid = user["id"]
    async with signed_in_client(app, "viewer@example.com") as viewer:
        for method, path, body in [
            ("GET", "/api/users", None),
            ("POST", "/api/users", {"email": "x@example.com", "name": "X"}),
            ("GET", f"/api/users/{uid}", None),
            ("PATCH", f"/api/users/{uid}", {"name": "X"}),
            ("POST", f"/api/users/{uid}/disable", None),
            ("POST", f"/api/users/{uid}/enable", None),
            ("POST", f"/api/users/{uid}/reset-link", None),
        ]:
            r = await viewer.request(method, path, json=body)
            assert_error(r, 403, "forbidden")


async def test_users_need_credentials(anon: httpx.AsyncClient) -> None:
    assert_error(await anon.get("/api/users"), 401, "unauthorized")


# Keeping the token out of logs and traces


async def test_access_log_shows_the_template_not_the_token(
    admin: httpx.AsyncClient, anon: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    _, token = await create(admin)
    caplog.set_level(logging.INFO, logger="megooflags_api.access")
    caplog.clear()
    assert (await anon.get(f"/api/invitations/{token}")).status_code == 200
    assert (await accept(anon, token)).status_code == 204
    access = [r for r in caplog.records if r.name == "megooflags_api.access"]
    assert [r.getMessage() for r in access] == [
        "GET /api/invitations/{token} 200",
        "POST /api/invitations/{token} 204",
    ]
    # httpx logs the URL it requested on the client side; the server never logs the token.
    server = [r for r in caplog.records if r.name.startswith("megooflags_api")]
    assert all(token not in str(vars(r)) for r in server)


def test_invitations_are_not_traced() -> None:
    assert "/api/invitations" in EXCLUDED_URLS.split(",")

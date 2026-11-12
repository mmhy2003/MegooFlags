"""Per-user UI preferences: theme and sidebar state."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import ORIGIN, assert_error, audit_entries, bearer, seed_user, sign_in

from megooflags_api.models import User
from megooflags_api.models.enums import UserTheme

PREFERENCES = "/api/account/preferences"
DEFAULTS = {"theme": "system", "sidebar_collapsed": False}


async def test_new_user_has_default_preferences(app: FastAPI) -> None:
    uid = await seed_user(app)

    async with app.state.sessionmaker() as session:
        user = await session.get(User, uid)

    assert user is not None
    assert user.theme == UserTheme.SYSTEM
    assert user.sidebar_collapsed is False


@asynccontextmanager
async def signed_in_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=ORIGIN
    ) as client:
        r = await sign_in(client)
        assert r.status_code == 200, r.text
        yield client


async def patch(client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    return await client.patch(PREFERENCES, json=body)


async def test_me_returns_default_preferences(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        r = await client.get("/api/auth/me")

    assert r.status_code == 200, r.text
    assert r.json()["preferences"] == DEFAULTS


async def test_patch_changes_only_given_fields(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        first = await patch(client, {"theme": "dark"})
        second = await patch(client, {"sidebar_collapsed": True})
        me = await client.get("/api/auth/me")

    assert first.status_code == 200, first.text
    assert first.json() == {"theme": "dark", "sidebar_collapsed": False}
    assert second.status_code == 200, second.text
    assert second.json() == {"theme": "dark", "sidebar_collapsed": True}
    assert me.json()["preferences"] == {"theme": "dark", "sidebar_collapsed": True}


async def test_patch_with_empty_body_returns_current(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        await patch(client, {"theme": "light"})
        r = await patch(client, {})

    assert r.status_code == 200, r.text
    assert r.json() == {"theme": "light", "sidebar_collapsed": False}


@pytest.mark.parametrize(
    "body", [{"theme": "neon"}, {"sidebar_collapsed": "maybe"}, {"colour": "x"}]
)
async def test_patch_rejects_bad_values(app: FastAPI, body: dict[str, Any]) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        r = await patch(client, body)
        me = await client.get("/api/auth/me")

    assert_error(r, 422, "validation_error")
    assert me.json()["preferences"] == DEFAULTS


async def test_tokens_cannot_patch_preferences(api: httpx.AsyncClient) -> None:
    assert_error(await patch(api, {"theme": "dark"}), 403, "forbidden")


async def test_token_me_has_no_preferences(app: FastAPI, api: httpx.AsyncClient) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        created = await client.post(
            "/api/account/tokens", json={"name": "ci", "permissions": ["project.read"]}
        )
    assert created.status_code == 201, created.text
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=bearer(created.json()["secret"])
    ) as personal:
        personal_me = await personal.get("/api/auth/me")
    service_me = await api.get("/api/auth/me")

    assert personal_me.status_code == 200, personal_me.text
    assert personal_me.json()["kind"] != "user"
    assert personal_me.json()["preferences"] is None
    assert service_me.status_code == 200, service_me.text
    assert service_me.json()["preferences"] is None


async def test_patch_writes_no_audit_entry(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        before = await audit_entries(app)
        r = await patch(client, {"theme": "dark", "sidebar_collapsed": True})

    assert r.status_code == 200, r.text
    assert await audit_entries(app) == before


async def test_preferences_follow_the_user_to_a_new_session(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in_client(app) as client:
        await patch(client, {"theme": "light", "sidebar_collapsed": True})
    async with signed_in_client(app) as other:
        r = await other.get("/api/auth/me")

    assert r.json()["preferences"] == {"theme": "light", "sidebar_collapsed": True}

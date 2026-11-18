"""GET /api/system/version: the running version, and a newer release for org admins."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from conftest import close_app
from fastapi import FastAPI
from helpers import bearer, reset_database, seed_service_token

from megooflags_api import __version__
from megooflags_api.main import create_app
from megooflags_api.models import UpdateStatus
from megooflags_api.settings import Settings


@asynccontextmanager
async def app_with(*, update_check: bool, latest: str) -> AsyncIterator[FastAPI]:
    """An app with the update check on or off (the tests turn it off by default), where the last
    check stored `latest`."""
    app = create_app(Settings(update_check=update_check))
    await reset_database(app)
    try:
        async with app.state.sessionmaker() as session:
            row = await session.get(UpdateStatus, 1)
            assert row is not None
            row.latest_version = latest
            await session.commit()
        yield app
    finally:
        await app.state.ruleset_cache.aclose()
        await close_app(app)


async def get_version(app: FastAPI, role: str = "admin") -> httpx.Response:
    headers = bearer(await seed_service_token(app, role))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as client:
        return await client.get("/api/system/version")


async def test_admin_sees_update(migrated: None) -> None:
    async with app_with(update_check=True, latest="9.9.9") as app:
        response = await get_version(app)

    assert response.status_code == 200
    assert response.json() == {
        "version": __version__,
        "update": {
            "version": "9.9.9",
            "url": "https://github.com/mmhy2003/MegooFlags/releases/tag/v9.9.9",
        },
    }


async def test_viewer_sees_no_update(migrated: None) -> None:
    async with app_with(update_check=True, latest="9.9.9") as app:
        response = await get_version(app, "viewer")

    assert response.status_code == 200
    assert response.json() == {"version": __version__, "update": None}


async def test_no_update_when_not_newer(migrated: None) -> None:
    async with app_with(update_check=True, latest="0.0.1") as app:
        response = await get_version(app)

    assert response.status_code == 200
    assert response.json() == {"version": __version__, "update": None}


async def test_check_off_hides_stored_update(migrated: None) -> None:
    async with app_with(update_check=False, latest="9.9.9") as app:
        response = await get_version(app)

    assert response.status_code == 200
    assert response.json() == {"version": __version__, "update": None}


async def test_needs_credentials(anon: httpx.AsyncClient) -> None:
    response = await anon.get("/api/system/version")

    assert response.status_code == 401

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from megooflags_api.main import create_app
from megooflags_api.settings import Settings

API_DIR = Path(__file__).resolve().parents[1]

ClientFactory = Callable[[Settings | None], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def client_for() -> ClientFactory:
    @asynccontextmanager
    async def factory(settings: Settings | None = None) -> AsyncIterator[httpx.AsyncClient]:
        app = create_app(settings)
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client
        finally:
            await app.state.valkey.aclose()
            await app.state.engine.dispose()

    return factory


@pytest.fixture(scope="session")
def valkey_up() -> None:
    if "MEGOOFLAGS_VALKEY_URL" not in os.environ:
        pytest.skip("MEGOOFLAGS_VALKEY_URL is not set")


@pytest.fixture(scope="session")
def migrated() -> None:
    if "MEGOOFLAGS_DATABASE_URL" not in os.environ:
        pytest.skip("MEGOOFLAGS_DATABASE_URL is not set")
    # Subprocess, not alembic.command: env.py calls fileConfig, which would reset test logging.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=API_DIR, check=True)


@pytest.fixture
async def app(migrated: None) -> AsyncIterator[FastAPI]:
    app = create_app(Settings())
    async with app.state.engine.begin() as conn:
        await conn.execute(text("TRUNCATE project CASCADE"))  # every table hangs off project
    try:
        yield app
    finally:
        await app.state.ruleset_cache.aclose()
        await app.state.valkey.aclose()
        await app.state.engine.dispose()


@pytest.fixture
async def api(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

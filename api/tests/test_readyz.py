import os
import time

import pytest
from conftest import ClientFactory

from megooflags_api.settings import Settings

UNREACHABLE_DB = "postgresql+asyncpg://megooflags:x@127.0.0.1:1/megooflags"
UNREACHABLE_VALKEY = "redis://127.0.0.1:1/0"
needs_db_and_valkey = pytest.mark.skipif(
    "MEGOOFLAGS_DATABASE_URL" not in os.environ or "MEGOOFLAGS_VALKEY_URL" not in os.environ,
    reason="MEGOOFLAGS_DATABASE_URL or MEGOOFLAGS_VALKEY_URL is not set",
)


async def test_readyz_db_down(client_for: ClientFactory) -> None:
    settings = Settings(database_url=UNREACHABLE_DB, db_connect_timeout=1)
    async with client_for(settings) as client:
        started = time.monotonic()
        response = await client.get("/readyz")
        elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert elapsed < 5


@needs_db_and_valkey
async def test_readyz_db_up(client_for: ClientFactory) -> None:
    async with client_for(Settings()) as client:
        response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@needs_db_and_valkey
async def test_readyz_valkey_down(client_for: ClientFactory) -> None:
    async with client_for(Settings(valkey_url=UNREACHABLE_VALKEY)) as client:
        started = time.monotonic()
        response = await client.get("/readyz")
        elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert elapsed < 5

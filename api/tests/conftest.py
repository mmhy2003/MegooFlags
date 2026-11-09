from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest

from megooflags_api.main import create_app
from megooflags_api.settings import Settings

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
            await app.state.engine.dispose()

    return factory

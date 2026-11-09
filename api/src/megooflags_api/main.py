from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from megooflags_api import __version__
from megooflags_api.db import create_engine
from megooflags_api.routers import health
from megooflags_api.settings import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="MegooFlags API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = create_engine(settings)
    app.include_router(health.router)
    return app


app = create_app()

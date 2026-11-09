from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from megooflags_api import __version__, metrics
from megooflags_api.db import create_engine
from megooflags_api.logging import AccessLogMiddleware, configure_logging
from megooflags_api.routers import health
from megooflags_api.settings import Settings, get_settings
from megooflags_api.tracing import configure_tracing


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    if app.state.tracer_provider is not None:
        app.state.tracer_provider.shutdown()
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    app = FastAPI(title="MegooFlags API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = create_engine(settings)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(metrics.MetricsMiddleware)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.state.tracer_provider = configure_tracing(app, app.state.engine)
    return app


app = create_app()

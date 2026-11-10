import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI

from megooflags_api import __version__, metrics
from megooflags_api.db import create_engine, create_sessionmaker
from megooflags_api.deps import get_actor
from megooflags_api.errors import install_error_handlers
from megooflags_api.logging import AccessLogMiddleware, configure_logging
from megooflags_api.routers import (
    environments,
    evaluate,
    flag_environments,
    flags,
    health,
    projects,
    sdk_keys,
    segments,
)
from megooflags_api.services.rate_limit import RateLimiter
from megooflags_api.services.ruleset_cache import RulesetCache
from megooflags_api.services.sdk_keys import KeyResolver
from megooflags_api.settings import Settings, get_settings
from megooflags_api.tracing import configure_tracing
from megooflags_api.valkey import check_valkey, create_valkey


def background_jobs(app: FastAPI) -> list[Coroutine[Any, Any, None]]:
    return []


async def shutdown_jobs(app: FastAPI) -> None:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await check_valkey(app.state.valkey, app.state.settings.valkey_url)
    tasks = [asyncio.create_task(job) for job in background_jobs(app)]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await shutdown_jobs(app)
        await app.state.valkey.aclose()
        if app.state.tracer_provider is not None:
            app.state.tracer_provider.shutdown()
        await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    app = FastAPI(title="MegooFlags API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = create_engine(settings)
    app.state.sessionmaker = create_sessionmaker(app.state.engine)
    app.state.valkey = create_valkey(settings.valkey_url)
    app.state.ruleset_cache = RulesetCache()
    app.state.key_resolver = KeyResolver()
    app.state.rate_limiter = RateLimiter(app.state.valkey, settings.client_rate_limit_per_minute)
    install_error_handlers(app)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(metrics.MetricsMiddleware)
    app.include_router(health.router)
    app.include_router(metrics.router)
    for module in (
        projects,
        environments,
        flags,
        flag_environments,
        segments,
        evaluate,
        sdk_keys,
    ):
        app.include_router(module.router, prefix="/api", dependencies=[Depends(get_actor)])
    app.state.tracer_provider = configure_tracing(app, app.state.engine)
    return app


app = create_app()

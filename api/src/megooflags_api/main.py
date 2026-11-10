import asyncio
import logging
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI

from megooflags_api import __version__, metrics
from megooflags_api.auth.principal import get_principal
from megooflags_api.auth.throttle import AuthThrottle
from megooflags_api.cors import SdkCorsMiddleware
from megooflags_api.crypto import SecretBox, require_secret_key
from megooflags_api.db import create_engine, create_sessionmaker
from megooflags_api.deps import get_actor
from megooflags_api.errors import install_error_handlers
from megooflags_api.logging import AccessLogMiddleware, configure_logging
from megooflags_api.routers import (
    auth,
    environments,
    evaluate,
    flag_environments,
    flags,
    health,
    ofrep,
    projects,
    roles,
    sdk,
    sdk_keys,
    segments,
    setup,
)
from megooflags_api.services.changes import ChangeBus
from megooflags_api.services.events import (
    EvaluationCounter,
    EventsBodyLimitMiddleware,
    run_retention,
)
from megooflags_api.services.rate_limit import RateLimiter
from megooflags_api.services.ruleset_cache import RulesetCache
from megooflags_api.services.sdk_keys import KeyResolver
from megooflags_api.services.streams import StreamHub
from megooflags_api.settings import Settings, get_settings
from megooflags_api.tracing import configure_tracing
from megooflags_api.valkey import check_valkey, create_valkey

logger = logging.getLogger(__name__)


def background_jobs(app: FastAPI) -> list[Coroutine[Any, Any, None]]:
    return [
        app.state.change_bus.run(),
        app.state.stream_hub.run_recheck_loop(app.state.settings.stream_recheck_seconds),
        app.state.evaluation_counter.run(app.state.sessionmaker),
        run_retention(app.state.sessionmaker, app.state.settings.exposure_retention_days),
    ]


async def shutdown_jobs(app: FastAPI) -> None:
    await app.state.evaluation_counter.flush(app.state.sessionmaker)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    require_secret_key(app.state.settings)
    await check_valkey(app.state.valkey, app.state.settings.valkey_url)
    try:
        await health.warn_if_no_user(app.state.sessionmaker)
    except Exception:
        # The database may not be reachable yet; /readyz repeats the check.
        logger.debug("cannot check for users at startup", exc_info=True)
    tasks = [asyncio.create_task(job) for job in background_jobs(app)]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app.state.ruleset_cache.aclose()
        await shutdown_jobs(app)
        await app.state.valkey.aclose()
        if app.state.tracer_provider is not None:
            app.state.tracer_provider.shutdown()
        await app.state.engine.dispose()
        metrics.worker_exited()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)
    app = FastAPI(title="MegooFlags API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    # None without a key, so export_openapi works; the lifespan refuses to start without one.
    app.state.secret_box = (
        SecretBox(settings.secret_key.get_secret_value()) if settings.secret_key else None
    )
    app.state.engine = create_engine(settings)
    app.state.sessionmaker = create_sessionmaker(app.state.engine)
    app.state.valkey = create_valkey(settings.valkey_url)
    app.state.ruleset_cache = RulesetCache(app.state.sessionmaker)
    app.state.key_resolver = KeyResolver()
    app.state.auth_throttle = AuthThrottle(app.state.valkey)
    app.state.rate_limiter = RateLimiter(app.state.valkey, settings.client_rate_limit_per_minute)
    app.state.change_bus = ChangeBus(app.state.valkey, settings.valkey_url)
    app.state.change_bus.on_project.append(app.state.ruleset_cache.invalidate)
    app.state.change_bus.on_key_revoked.append(app.state.key_resolver.forget)
    app.state.stream_hub = StreamHub(app.state.sessionmaker, app.state.ruleset_cache)
    app.state.change_bus.on_project.append(app.state.stream_hub.notify_project)
    app.state.change_bus.on_key_revoked.append(app.state.stream_hub.notify_key_revoked)
    app.state.change_bus.on_resubscribe.append(app.state.stream_hub.recheck_all)
    app.state.evaluation_counter = EvaluationCounter()
    install_error_handlers(app)
    app.add_middleware(EventsBodyLimitMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(metrics.MetricsMiddleware)
    app.add_middleware(SdkCorsMiddleware)
    app.include_router(health.router)
    app.include_router(metrics.router)
    # Public or self-checking routes (PUBLIC_ROUTES): no get_principal on the router.
    app.include_router(setup.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
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
    app.include_router(roles.router, prefix="/api", dependencies=[Depends(get_principal)])
    app.include_router(sdk.router)
    app.include_router(ofrep.router)
    app.state.tracer_provider = configure_tracing(app, app.state.engine)
    return app


app = create_app()

"""The worker application (`MEGOOFLAGS_ROLE=worker`): `/sdk/*`, `/ofrep/*`, health and metrics.

It has no database, no Valkey and no `/api/*` routes. The SDK and OFREP routers are the same as
on the control plane; here they read the snapshot store through `Delivery`, and answer 503 while
the worker has no valid snapshot or its node token was revoked. The lifespan loads the store and
the event buffer from `data_dir`, so a restarted worker serves its last snapshot at once, and runs
the link to the control plane in the background.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from megooflags_api import __version__, metrics
from megooflags_api.cors import SdkCorsMiddleware
from megooflags_api.errors import install_error_handlers
from megooflags_api.logging import AccessLogMiddleware
from megooflags_api.routers import health, ofrep, sdk
from megooflags_api.services.cluster.config import worker_config_errors
from megooflags_api.services.delivery import Delivery
from megooflags_api.services.events import EventsBodyLimitMiddleware
from megooflags_api.services.streams import StreamHub
from megooflags_api.services.worker.buffer import EventBuffer
from megooflags_api.services.worker.client import ControlPlaneClient
from megooflags_api.services.worker.sources import (
    BufferEventSink,
    MemoryRateLimiter,
    StoreKeySource,
    StoreRulesetSource,
    StoreStreamSource,
)
from megooflags_api.services.worker.state import WorkerState
from megooflags_api.services.worker.store import SnapshotStore
from megooflags_api.settings import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    errors = worker_config_errors(app.state.settings)
    if errors:
        raise RuntimeError("; ".join(errors))
    client: ControlPlaneClient = app.state.worker_client
    store: SnapshotStore = app.state.store
    buffer: EventBuffer = app.state.event_buffer
    state: WorkerState = app.state.worker_state
    hub: StreamHub = app.state.stream_hub
    await asyncio.to_thread(store.load)
    await asyncio.to_thread(buffer.load)

    def update_metrics() -> None:
        age = state.snapshot_age()
        metrics.WORKER_SNAPSHOT_AGE.set(float("nan") if age is None else max(age, 0.0))
        metrics.WORKER_SNAPSHOT_VERSION.set(store.max_version())
        metrics.WORKER_CONTROL_PLANE_CONNECTED.set(1 if state.connected else 0)
        metrics.WORKER_BUFFERED_EVENTS.set(buffer.pending_events)

    metrics.BEFORE_RENDER.append(update_metrics)
    jobs = [
        client.run(),
        client.heartbeat_loop(),
        buffer.run_sealer(),
        buffer.run_forwarder(client.send_events),
        hub.run_recheck_loop(app.state.settings.stream_recheck_seconds),
    ]
    tasks = [asyncio.create_task(job) for job in jobs]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        metrics.BEFORE_RENDER.remove(update_metrics)
        await asyncio.to_thread(buffer.seal)
        await asyncio.to_thread(store.save_meta)
        await client.aclose()


def create_worker_app(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    app = FastAPI(title="MegooFlags API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    store = SnapshotStore(settings.data_dir)
    state = WorkerState(store)
    buffer = EventBuffer(settings.data_dir / "buffer", settings.worker_buffer_max_mb * 1024 * 1024)
    # With a source the hub never uses the database arguments.
    hub = StreamHub(None, None, source=StoreStreamSource(store))  # type: ignore[arg-type]
    app.state.store = store
    app.state.worker_state = state
    app.state.event_buffer = buffer
    app.state.stream_hub = hub
    app.state.delivery = Delivery(
        StoreRulesetSource(store),
        StoreKeySource(store),
        BufferEventSink(buffer, lambda: store.meta.persist_context),
        MemoryRateLimiter(settings.client_rate_limit_per_minute),
        unavailable=state.unavailable_reason,
    )
    # Without a URL or token there is no client; the lifespan refuses to start and says why.
    app.state.worker_client = (
        None
        if worker_config_errors(settings)
        else ControlPlaneClient(settings, store, state, hub, buffer, transport=transport)
    )
    install_error_handlers(app)
    app.add_middleware(EventsBodyLimitMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(metrics.MetricsMiddleware)
    app.add_middleware(SdkCorsMiddleware)
    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(sdk.router)
    app.include_router(ofrep.router)
    return app

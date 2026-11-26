"""Prometheus metrics. With several uvicorn workers, `serve` sets `PROMETHEUS_MULTIPROC_DIR` and
each worker writes its values there, so `/metrics` on any worker reports the whole container."""

import os
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)
from starlette.types import ASGIApp, Receive, Scope, Send

from megooflags_api import __version__
from megooflags_api.asgi import ResponseStatus, route_label

REQUESTS = Counter(
    "megooflags_http_requests", "HTTP requests handled.", ["method", "route", "status"]
)
DURATION = Histogram(
    "megooflags_http_request_duration_seconds", "HTTP request duration.", ["method", "route"]
)
IN_PROGRESS = Gauge(
    "megooflags_http_requests_in_progress",
    "HTTP requests in progress.",
    multiprocess_mode="livesum",
)
RATE_LIMIT_ERRORS = Counter(
    "megooflags_rate_limit_errors", "Client rate-limit checks that failed open (Valkey errors)."
)
CHANGE_PUBLISH_ERRORS = Counter(
    "megooflags_change_publish_errors",
    "Change messages that could not be published to Valkey (the periodic re-check delivers them).",
)
SCHEDULER_LEADER = Gauge(
    "megooflags_scheduler_leader",
    "1 while this process is the background scheduler's leader.",
    multiprocess_mode="livesum",
)
SCHEDULER_JOBS = Counter(
    "megooflags_scheduler_jobs",
    "Rows the scheduler's jobs handled, by outcome (ok, failed, or error for a job that raised).",
    ["job", "outcome"],
)
INTEGRATION_DELIVERIES = Counter(
    "megooflags_integration_deliveries_total",
    "Integration delivery attempts by kind and outcome.",
    ["kind", "outcome"],
)
NODE_STREAMS = Gauge(
    "megooflags_node_streams",
    "Open worker streams on this control-plane process.",
    multiprocess_mode="livesum",
)
WORKER_SNAPSHOT_AGE = Gauge(
    "megooflags_worker_snapshot_age_seconds",
    "Seconds since the worker last heard from the control plane while in sync.",
)
WORKER_SNAPSHOT_VERSION = Gauge(
    "megooflags_worker_snapshot_version", "Highest snapshot version the worker has applied."
)
WORKER_CONTROL_PLANE_CONNECTED = Gauge(
    "megooflags_worker_control_plane_connected",
    "1 while the worker's stream to the control plane is open.",
)
WORKER_BUFFERED_EVENTS = Gauge(
    "megooflags_worker_buffered_events", "Exposures and counts waiting to be forwarded."
)
WORKER_EVENTS_DROPPED = Counter(
    "megooflags_worker_events_dropped",
    "Exposures dropped because the buffer was full or older than 7 days.",
)
BUILD_INFO = Gauge(
    "megooflags_build_info", "MegooFlags API build.", ["version"], multiprocess_mode="max"
)
BUILD_INFO.labels(version=__version__).set(1)

MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"

router = APIRouter()

# (method, route, status) -> its REQUESTS and DURATION children, so a request skips labels().
_CHILDREN: dict[tuple[str, str, int], tuple[Any, Any]] = {}

# Called by render() first, so gauges read from live state are current (the worker's).
BEFORE_RENDER: list[Callable[[], None]] = []


def render() -> bytes:
    """The metrics of this process, or of every worker when several share a metrics directory."""
    for callback in BEFORE_RENDER:
        callback()
    if not os.environ.get(MULTIPROC_ENV):
        return generate_latest(REGISTRY)
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    return generate_latest(registry)


def worker_exited() -> None:
    """Drop this worker's live gauges, so a stopped worker no longer counts as busy."""
    if os.environ.get(MULTIPROC_ENV):
        multiprocess.mark_process_dead(os.getpid())  # type: ignore[no-untyped-call]


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(render(), media_type=CONTENT_TYPE_LATEST)


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = ResponseStatus(send)
        started = time.perf_counter()
        IN_PROGRESS.inc()
        try:
            await self.app(scope, receive, status.send)
        finally:
            IN_PROGRESS.dec()
            route = route_label(scope)
            method = scope["method"]
            key = (method, route, status.code)
            children = _CHILDREN.get(key)
            if children is None:
                children = _CHILDREN[key] = (
                    REQUESTS.labels(method=method, route=route, status=str(status.code)),
                    DURATION.labels(method=method, route=route),
                )
            requests, duration = children
            requests.inc()
            duration.observe(time.perf_counter() - started)

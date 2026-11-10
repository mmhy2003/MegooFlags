import time

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.types import ASGIApp, Receive, Scope, Send

from megooflags_api import __version__
from megooflags_api.asgi import ResponseStatus, route_label

REQUESTS = Counter(
    "megooflags_http_requests", "HTTP requests handled.", ["method", "route", "status"]
)
DURATION = Histogram(
    "megooflags_http_request_duration_seconds", "HTTP request duration.", ["method", "route"]
)
IN_PROGRESS = Gauge("megooflags_http_requests_in_progress", "HTTP requests in progress.")
RATE_LIMIT_ERRORS = Counter(
    "megooflags_rate_limit_errors", "Client rate-limit checks that failed open (Valkey errors)."
)
CHANGE_PUBLISH_ERRORS = Counter(
    "megooflags_change_publish_errors",
    "Change messages that could not be published to Valkey (the periodic re-check delivers them).",
)
BUILD_INFO = Gauge("megooflags_build_info", "MegooFlags API build.", ["version"])
BUILD_INFO.labels(version=__version__).set(1)

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
            REQUESTS.labels(method=method, route=route, status=str(status.code)).inc()
            DURATION.labels(method=method, route=route).observe(time.perf_counter() - started)

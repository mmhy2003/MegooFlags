# MF-75 Implementation Plan: Single-node Compose bundle with TLS modes, scaling and observability

**Goal:** `docker compose up` with `deploy/` gives a working stack in plain-HTTP mode (`MEGOOFLAGS_SITE_ADDRESS` empty, port 80 only) or automatic-HTTPS mode (a domain is set). API replicas are load-balanced. The API exposes Prometheus metrics, OpenTelemetry traces and JSON logs. CI smoke-tests both modes.

**Architecture:** The API gets small single-purpose modules: `urls.py` (public base URL), `asgi.py` (shared ASGI helpers), `metrics.py`, `tracing.py` and `logging.py`. `create_app` wires them together. Two pure-ASGI middlewares record metrics and access logs using the route template. The bundle changes are in `deploy/`: 443 is published only when the site address is set, Caddy uses dynamic A-record upstreams for `api`, and `/metrics` returns 404 at the edge. `scripts/smoke.sh` takes a mode, `http` or `https`, and CI runs both modes as a matrix.

**Conventions (from AGENTS.md):** uv only. Commit `pyproject.toml` and `uv.lock` together. Every user-visible change gets a CHANGELOG line. `api/openapi.json` must not change. Lint with ruff, run mypy in strict mode on `src`.

Run all API commands from `api/`. The checks every API step must pass:
```bash
cd api && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest
cd api && uv run python -m megooflags_api.export_openapi openapi.json && git diff --exit-code openapi.json
```

## File map

| File | Action | Responsibility |
|---|---|---|
| `api/src/megooflags_api/settings.py` | modify | Adds `public_url`, `log_level` and `log_format`. An empty `MEGOOFLAGS_PUBLIC_URL` becomes `None`. |
| `api/src/megooflags_api/urls.py` | create | `public_base_url(request)` (DEP-2) |
| `api/src/megooflags_api/asgi.py` | create | `route_label(scope)` and `ResponseStatus` (a send wrapper that captures the status), used by the metrics and logging middlewares |
| `api/src/megooflags_api/metrics.py` | create | Prometheus metrics, `MetricsMiddleware`, and the `/metrics` router (left out of the schema) |
| `api/src/megooflags_api/tracing.py` | create | `configure_tracing(app, engine, exporter=None)`, which does nothing unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set |
| `api/src/megooflags_api/logging.py` | create | `JsonFormatter`, `logging_config()`, `configure_logging(settings)` and `AccessLogMiddleware` |
| `api/log_config.json` | create | Uvicorn `--log-config`: JSON formatter from process start |
| `api/src/megooflags_api/main.py` | modify | Wires logging, middlewares, the metrics router and tracing. Shuts the tracer provider down on exit. |
| `api/pyproject.toml`, `api/uv.lock` | modify | prometheus-client and the OpenTelemetry packages. A mypy override if it is needed. |
| `api/Dockerfile` | modify | `FORWARDED_ALLOW_IPS` ENV, `--no-access-log`, `--log-config`, and copies `log_config.json` |
| `api/tests/test_urls.py`, `test_metrics.py`, `test_tracing.py`, `test_logging.py` | create | pytest tests |
| `deploy/docker-compose.yml` | modify | Publishes 443 conditionally. New api env vars. |
| `deploy/Caddyfile` | modify | `respond /metrics 404`, dynamic upstreams, round robin, retries |
| `deploy/.env.example` | modify | Empty site address, docs for both modes, new variables, Prometheus snippet |
| `deploy/compose.ci.yml` | modify | Also maps `${MEGOOFLAGS_HTTPS_PORT:-8443}:443` |
| `scripts/smoke.sh` | modify | `http` or `https` mode, metrics, logs, scaling and memory checks |
| `.github/workflows/ci.yml` | modify | Smoke job runs a matrix over `mode: [http, https]` |
| `CHANGELOG.md` | modify | `[Unreleased]` entries |

## Steps

### 1. Settings and public base URL (DEP-2, DEP-3 forwarded headers)
Modify `api/src/megooflags_api/settings.py` and create `api/src/megooflags_api/urls.py` (Appendix A). Write `api/tests/test_urls.py` (Appendix A) first.

- Run `cd api && uv run pytest tests/test_urls.py -v`. It should fail with `ModuleNotFoundError: megooflags_api.urls`.
- Implement, then run it again. All four tests should pass.
- Run the full API checks. `openapi.json` must not change.
- Commit `feat(api): MEGOOFLAGS_PUBLIC_URL and public_base_url helper (MF-75)`.

### 2. Prometheus metrics with route-template labels
1. Run `cd api && uv add prometheus-client`. This updates `pyproject.toml` and `uv.lock`.
2. Write `api/tests/test_metrics.py` (Appendix B). Run `uv run pytest tests/test_metrics.py -v`. It should fail with an import error, or a 404 on `/metrics`.
3. Create `asgi.py` and `metrics.py` (Appendix B) and wire them into `main.py` (Appendix F, the metrics parts only).
4. Run the tests again. They should pass.
5. Run the full checks. The OpenAPI drift check must stay clean, because `/metrics` uses `include_in_schema=False`.
6. Commit `feat(api): Prometheus /metrics with per-route request metrics (MF-75)`.

### 3. OpenTelemetry tracing, off unless configured
1. Run `cd api && uv add opentelemetry-sdk opentelemetry-exporter-otlp-proto-http opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-sqlalchemy`.
2. Write `api/tests/test_tracing.py` (Appendix C) and run it. It should fail with an import error.
3. Create `tracing.py` (Appendix C) and wire it into `main.py` (Appendix F, the tracing parts).
4. If mypy reports missing stubs or `py.typed` for `opentelemetry.instrumentation.*` or `opentelemetry.exporter.*`, add this to `pyproject.toml` and nothing broader:
   ```toml
   [[tool.mypy.overrides]]
   module = ["opentelemetry.instrumentation.*", "opentelemetry.exporter.*"]
   ignore_missing_imports = true
   ```
5. Run the tests and the full checks. They should pass.
6. Commit `feat(api): optional OpenTelemetry tracing via OTEL_* env (MF-75)`.

### 4. Structured JSON logs, access log, and the API image
1. Write `api/tests/test_logging.py` (Appendix D) and run it. It should fail with an import error.
2. Create `logging.py` and `api/log_config.json` (Appendix D), and wire them into `main.py` (Appendix F, complete).
3. Change `api/Dockerfile` as in Appendix E.
4. Run the tests and the full checks. They should pass.
5. Check the image:
   - Run `docker build -t megooflags-api:ci api`.
   - Run `docker run --rm -d --name mf-log -e MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://x:x@127.0.0.1:1/x megooflags-api:ci`, wait about 5 seconds, then run `docker logs mf-log`. Every line must be JSON with `"logger": "uvicorn.error"`, for example `Uvicorn running on`.
   - Run `docker exec mf-log printenv FORWARDED_ALLOW_IPS`. It should show the private ranges.
   - Run `docker rm -f mf-log`.
6. Run `bash scripts/smoke.sh`. It is unchanged so far and should still pass.
7. Commit `feat(api): JSON logs, access-log middleware, trusted forwarded IPs (MF-75)`.

### 5. Compose bundle: TLS by site address, load-balanced API, internal metrics
Apply Appendix G to `deploy/docker-compose.yml`, `deploy/Caddyfile`, `deploy/.env.example` and `deploy/compose.ci.yml`. Then verify (see the step detail). Commit `feat(deploy): publish 443 only for a site domain; balance api replicas (MF-75)`.

### 6. Smoke test for both TLS modes
Replace `scripts/smoke.sh` with Appendix H. Run `bash scripts/smoke.sh http`, then `bash scripts/smoke.sh https`. Both should end with `smoke OK (<mode>)`. Commit `test(deploy): smoke-test plain HTTP and automatic HTTPS modes (MF-75)`.

### 7. CI smoke matrix
Change the `smoke` job in `.github/workflows/ci.yml` as in Appendix I. Run actionlint. Commit `ci: run the compose smoke test in http and https modes (MF-75)`.

### 8. CHANGELOG
Add the Appendix J entries under `## [Unreleased]`. Commit `docs: changelog for MF-75`.

## Final verification
```bash
cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && git diff --exit-code -- api/openapi.json
MEGOOFLAGS_SITE_ADDRESS= docker compose -f deploy/docker-compose.yml config | grep -n 'published'
MEGOOFLAGS_SITE_ADDRESS=flags.example.com docker compose -f deploy/docker-compose.yml config | grep -n 'published'
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web
bash scripts/smoke.sh http && bash scripts/smoke.sh https
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```
- With the site address empty, `config` shows `published: "80"` only. The 443 entries have no `published` value, or an empty one.
- With a domain, it shows `published: "443"` for both tcp and udp.

---

## Appendix A: settings and URLs

`api/src/megooflags_api/settings.py`
```python
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEGOOFLAGS_", extra="ignore")

    database_url: str = "postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags"
    db_connect_timeout: float = 2.0
    public_url: AnyHttpUrl | None = None
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"

    @field_validator("public_url", mode="before")
    @classmethod
    def _empty_public_url_is_none(cls, value: object) -> object:
        # docker-compose passes MEGOOFLAGS_PUBLIC_URL="" when it is not configured.
        return None if value == "" else value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`api/src/megooflags_api/urls.py`
```python
from fastapi import Request

from megooflags_api.settings import Settings


def public_base_url(request: Request) -> str:
    """External base URL without a trailing slash.

    MEGOOFLAGS_PUBLIC_URL wins. Otherwise the URL comes from the request, whose scheme and client
    Uvicorn has already corrected from X-Forwarded-* headers sent by a trusted proxy.
    """
    settings: Settings = request.app.state.settings
    if settings.public_url is not None:
        return str(settings.public_url).rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}"
```

`api/tests/test_urls.py`
```python
from typing import Any

import httpx
import pytest
from fastapi import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from megooflags_api.main import create_app
from megooflags_api.settings import Settings
from megooflags_api.urls import public_base_url

# Same value as FORWARDED_ALLOW_IPS in api/Dockerfile and deploy/docker-compose.yml.
PRIVATE_RANGES = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1"
HEADERS = {
    "Host": "flags.example.com",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-For": "198.51.100.7",
}


async def probe(settings: Settings, client_ip: str) -> dict[str, Any]:
    app = create_app(settings)

    async def endpoint(request: Request) -> dict[str, str]:
        client = request.client.host if request.client else ""
        return {"url": public_base_url(request), "client": client}

    app.add_api_route("/_probe", endpoint)
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=PRIVATE_RANGES)
    transport = httpx.ASGITransport(app=wrapped, client=(client_ip, 4321))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/_probe", headers=HEADERS)
            return dict(response.json())
    finally:
        await app.state.engine.dispose()


async def test_public_url_setting_wins() -> None:
    result = await probe(Settings(public_url="https://flags.example.org/"), "172.18.0.5")
    assert result["url"] == "https://flags.example.org"


async def test_forwarded_headers_trusted_from_private_proxy() -> None:
    result = await probe(Settings(), "172.18.0.5")
    assert result == {"url": "https://flags.example.com", "client": "198.51.100.7"}


async def test_forwarded_headers_ignored_from_untrusted_ip() -> None:
    result = await probe(Settings(), "203.0.113.9")
    assert result == {"url": "http://flags.example.com", "client": "203.0.113.9"}


def test_empty_public_url_env_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEGOOFLAGS_PUBLIC_URL", "")
    assert Settings().public_url is None
```

## Appendix B: ASGI helpers and metrics

`api/src/megooflags_api/asgi.py`
```python
from starlette.types import Message, Scope, Send


def route_label(scope: Scope) -> str:
    """Route template ("/flags/{key}") for metrics and logs, or "other" when no route matched."""
    path = getattr(scope.get("route"), "path", None)
    return path if isinstance(path, str) else "other"


class ResponseStatus:
    """Wraps an ASGI `send` and remembers the response status (500 until a response starts)."""

    def __init__(self, send: Send) -> None:
        self.code = 500
        self._send = send

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.code = message["status"]
        await self._send(message)
```
FastAPI's `APIRoute.matches` puts `"route"` into the child scope, and Starlette's router `scope.update`s the same dict. So after `await self.app(...)` returns, the middleware sees the matched route.

`api/src/megooflags_api/metrics.py`
```python
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
```
The Counter named `megooflags_http_requests` is exposed as `megooflags_http_requests_total`. The default `REGISTRY` is used, and the module is imported once per process.

`api/tests/test_metrics.py`
```python
import json

import httpx
from conftest import ClientFactory
from prometheus_client import REGISTRY

from megooflags_api.export_openapi import render
from megooflags_api.main import create_app


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_metrics_counts_requests(client_for: ClientFactory) -> None:
    before = sample("megooflags_http_requests_total", method="GET", route="/healthz", status="200")
    async with client_for(None) as client:
        await client.get("/healthz")
        await client.get("/nope/123")
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    after = sample("megooflags_http_requests_total", method="GET", route="/healthz", status="200")
    assert after == before + 1
    assert sample("megooflags_http_requests_total", method="GET", route="other", status="404") >= 1
    assert "/nope/123" not in response.text
    for name in (
        "megooflags_build_info",
        "megooflags_http_request_duration_seconds_bucket",
        "megooflags_http_requests_in_progress",
    ):
        assert name in response.text


async def test_route_label_is_the_template() -> None:
    app = create_app()

    async def item(item_id: str) -> dict[str, str]:
        return {"id": item_id}

    app.add_api_route("/_items/{item_id}", item)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/_items/42")
    finally:
        await app.state.engine.dispose()

    labels = {"method": "GET", "route": "/_items/{item_id}", "status": "200"}
    assert sample("megooflags_http_requests_total", **labels) >= 1
    assert sample("megooflags_http_requests_total", method="GET", route="/_items/42", status="200") == 0


def test_metrics_not_in_openapi() -> None:
    assert "/metrics" not in json.loads(render())["paths"]
```

## Appendix C: tracing

`api/src/megooflags_api/tracing.py`
```python
import logging
import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from sqlalchemy.ext.asyncio import AsyncEngine

from megooflags_api import __version__

EXCLUDED_URLS = "/healthz,/readyz,/metrics"

logger = logging.getLogger(__name__)


def configure_tracing(
    app: FastAPI, engine: AsyncEngine, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Trace requests and SQL when OTEL_EXPORTER_OTLP_ENDPOINT is set; otherwise do nothing.

    The OTLP/HTTP exporter reads the standard OTEL_EXPORTER_OTLP_* variables. Export failures are
    logged by the SDK and never fail a request.
    """
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return None
    resource = Resource.create(
        {
            SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME") or "megooflags-api",
            SERVICE_VERSION: __version__,
        }
    )
    provider = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(_ratio())))
    provider.add_span_processor(BatchSpanProcessor(exporter or OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls=EXCLUDED_URLS)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine, tracer_provider=provider)
    return provider


def _ratio() -> float:
    raw = os.environ.get("OTEL_TRACES_SAMPLER_ARG") or "1.0"
    try:
        return min(max(float(raw), 0.0), 1.0)
    except ValueError:
        logger.warning("invalid OTEL_TRACES_SAMPLER_ARG %r, sampling every trace", raw)
        return 1.0
```

`api/tests/test_tracing.py`
```python
import httpx
import pytest
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from megooflags_api.main import create_app
from megooflags_api.tracing import configure_tracing


async def test_tracing_is_off_without_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    app = create_app()
    try:
        assert app.state.tracer_provider is None
    finally:
        await app.state.engine.dispose()


async def test_tracing_records_spans_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    app = create_app()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    exporter = InMemorySpanExporter()
    provider = configure_tracing(app, app.state.engine, exporter)
    assert provider is not None
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/healthz")
            provider.force_flush()
            assert exporter.get_finished_spans() == ()
            await client.get("/openapi.json")
            provider.force_flush()
        spans = exporter.get_finished_spans()
        assert spans
        assert spans[0].resource.attributes["service.name"] == "megooflags-api"
    finally:
        provider.shutdown()
        SQLAlchemyInstrumentor().uninstrument()
        await app.state.engine.dispose()
```

## Appendix D: logging

`api/src/megooflags_api/logging.py` (with absolute imports, `import logging` inside it still resolves to the stdlib)
```python
import json
import logging
import logging.config
import time
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from starlette.types import ASGIApp, Receive, Scope, Send

from megooflags_api.asgi import ResponseStatus, route_label
from megooflags_api.settings import Settings

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "color_message"}
QUIET_ROUTES = frozenset({"/healthz", "/readyz", "/metrics"})
TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

access_logger = logging.getLogger("megooflags_api.access")


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, trace ids and any `extra` fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            entry["trace_id"] = format(span_context.trace_id, "032x")
            entry["span_id"] = format(span_context.span_id, "016x")
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def logging_config(level: str = "INFO", fmt: str = "json") -> dict[str, Any]:
    formatter: dict[str, Any] = {"()": JsonFormatter} if fmt == "json" else {"format": TEXT_FORMAT}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": formatter},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": level, "handlers": ["stdout"]},
        "loggers": {
            name: {"level": level, "handlers": [], "propagate": True}
            for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
        },
    }


def configure_logging(settings: Settings) -> None:
    logging.config.dictConfig(logging_config(settings.log_level, settings.log_format))


class AccessLogMiddleware:
    """Logs every HTTP request; health and metrics probes at DEBUG so they stay quiet."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = ResponseStatus(send)
        started = time.perf_counter()
        try:
            await self.app(scope, receive, status.send)
        finally:
            route = route_label(scope)
            client = scope.get("client")
            access_logger.log(
                logging.DEBUG if route in QUIET_ROUTES else logging.INFO,
                "%s %s %s",
                scope["method"],
                scope["path"],
                status.code,
                extra={
                    "method": scope["method"],
                    "route": route,
                    "status": status.code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "client_ip": client[0] if client else None,
                },
            )
```

`api/log_config.json` (the same shape as `logging_config("INFO", "json")`; `create_app` then re-applies it with `MEGOOFLAGS_LOG_LEVEL`/`MEGOOFLAGS_LOG_FORMAT`)
```json
{
  "version": 1,
  "disable_existing_loggers": false,
  "formatters": {"default": {"()": "megooflags_api.logging.JsonFormatter"}},
  "handlers": {
    "stdout": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stdout"}
  },
  "root": {"level": "INFO", "handlers": ["stdout"]},
  "loggers": {
    "uvicorn": {"level": "INFO", "handlers": [], "propagate": true},
    "uvicorn.error": {"level": "INFO", "handlers": [], "propagate": true},
    "uvicorn.access": {"level": "INFO", "handlers": [], "propagate": true}
  }
}
```

`api/tests/test_logging.py`
```python
import json
import logging
import logging.config
from pathlib import Path

import httpx
from opentelemetry.sdk.trace import TracerProvider

from megooflags_api.logging import JsonFormatter, access_logger
from megooflags_api.main import create_app
from megooflags_api.settings import Settings

API_DIR = Path(__file__).resolve().parents[1]


def record(msg: str = "hello %s", **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord("megooflags_api.test", logging.WARNING, __file__, 1, msg, ("world",), None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


def test_json_formatter_fields() -> None:
    entry = json.loads(JsonFormatter().format(record(route="/x")))
    assert entry["level"] == "WARNING"
    assert entry["logger"] == "megooflags_api.test"
    assert entry["msg"] == "hello world"
    assert entry["route"] == "/x"
    assert "ts" in entry
    assert "trace_id" not in entry


def test_json_formatter_adds_trace_ids_inside_a_span() -> None:
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        entry = json.loads(JsonFormatter().format(record()))
    ctx = span.get_span_context()
    assert entry["trace_id"] == format(ctx.trace_id, "032x")
    assert entry["span_id"] == format(ctx.span_id, "016x")


def test_log_config_file_uses_json_formatter() -> None:
    config = json.loads((API_DIR / "log_config.json").read_text(encoding="utf-8"))
    logging.config.dictConfig(config)
    assert isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)


def test_text_format_setting() -> None:
    app = create_app(Settings(log_format="text"))
    assert not isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)
    create_app(Settings())  # restore JSON for the rest of the session


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, rec: logging.LogRecord) -> None:
        self.records.append(rec)


async def test_access_log_fields_and_levels() -> None:
    app = create_app(Settings())
    capture = Capture()
    access_logger.addHandler(capture)
    access_logger.setLevel(logging.DEBUG)
    transport = httpx.ASGITransport(app=app, client=("198.51.100.7", 1234))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/healthz")
            await client.get("/api/nope")
    finally:
        access_logger.removeHandler(capture)
        access_logger.setLevel(logging.NOTSET)
        await app.state.engine.dispose()

    health, nope = capture.records
    assert (health.levelno, health.route, health.status) == (logging.DEBUG, "/healthz", 200)
    assert (nope.levelno, nope.route, nope.status) == (logging.INFO, "other", 404)
    assert nope.method == "GET" and nope.client_ip == "198.51.100.7"
    assert isinstance(nope.duration_ms, float)
```
(The `create_app` engines in `test_text_format_setting` are never connected, so not disposing them is harmless. If ruff flags the unused `app`, call `create_app(...)` without binding the result.)

## Appendix E: `api/Dockerfile` changes
Replace the last runtime lines (from `COPY alembic ./alembic` through `CMD`) with:
```dockerfile
COPY alembic ./alembic
COPY log_config.json ./
# Uvicorn reads FORWARDED_ALLOW_IPS for --proxy-headers: trust only the private (Docker) networks.
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1 \
    FORWARDED_ALLOW_IPS="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1"
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "megooflags_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log", "--log-config", "/app/log_config.json"]
```
Uvicorn runs one worker by default. Do not add `--workers`.

## Appendix F: `api/src/megooflags_api/main.py` (final state)
```python
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
```
- Step 2 adds only the `metrics` import, the `MetricsMiddleware` line and the `metrics.router` line.
- Step 3 adds the tracing import, `app.state.tracer_provider = ...` and the lifespan shutdown.
- Step 4 adds `configure_logging` and `AccessLogMiddleware`.

## Appendix G: deploy bundle

`deploy/docker-compose.yml`: replace the caddy `ports:` block and the api `environment:` block. Leave everything else unchanged.
```yaml
    ports:
      - "${MEGOOFLAGS_HTTP_PORT:-80}:80"
      # 443 is published on the host only when MEGOOFLAGS_SITE_ADDRESS holds a domain (automatic HTTPS).
      # Left empty (plain HTTP), Docker maps it to a random free host port where nothing listens,
      # so it never clashes with another proxy on :443.
      - target: 443
        published: "${MEGOOFLAGS_SITE_ADDRESS:+443}"
        protocol: tcp
      - target: 443
        published: "${MEGOOFLAGS_SITE_ADDRESS:+443}"
        protocol: udp
```
```yaml
  api:
    ...
    environment:
      MEGOOFLAGS_DATABASE_URL: ${MEGOOFLAGS_DATABASE_URL:-postgresql+asyncpg://megooflags:megooflags@db:5432/megooflags}
      MEGOOFLAGS_PUBLIC_URL: ${MEGOOFLAGS_PUBLIC_URL:-}
      MEGOOFLAGS_LOG_LEVEL: ${MEGOOFLAGS_LOG_LEVEL:-INFO}
      FORWARDED_ALLOW_IPS: ${FORWARDED_ALLOW_IPS:-10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1}
      OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-}
      OTEL_SERVICE_NAME: ${OTEL_SERVICE_NAME:-megooflags-api}
      OTEL_TRACES_SAMPLER_ARG: ${OTEL_TRACES_SAMPLER_ARG:-1.0}
```
**Fallback**, only if `docker compose config` or `up` rejects the empty `published`: replace both long-syntax entries with `"${MEGOOFLAGS_HTTPS_PORT:-443}:443"` and `"${MEGOOFLAGS_HTTPS_PORT:-443}:443/udp"`, document `MEGOOFLAGS_HTTPS_PORT` in `.env.example`, and say so in the commit message.

`deploy/Caddyfile`
```caddyfile
{
	servers {
		trusted_proxies static {$MEGOOFLAGS_TRUSTED_PROXIES:private_ranges}
	}
}

{$MEGOOFLAGS_SITE_ADDRESS::80} {
	# Metrics stay inside the compose network: Prometheus scrapes each api replica on :8000.
	respond /metrics 404

	@api path /api/* /sdk/* /ofrep/* /healthz /readyz
	reverse_proxy @api {
		# Every replica of `docker compose up --scale api=N`, re-resolved every 5s.
		dynamic a api 8000 {
			refresh 5s
		}
		lb_policy round_robin
		lb_try_duration 5s
		flush_interval -1
	}
	reverse_proxy web:3000
}
```

`deploy/compose.ci.yml`: the caddy override becomes
```yaml
  caddy:
    ports: !override
      - "${MEGOOFLAGS_HTTP_PORT:-8080}:80"
      - "${MEGOOFLAGS_HTTPS_PORT:-8443}:443"
```
(Update the header comment to "non-privileged ports".)

`deploy/.env.example`
```dotenv
# MegooFlags configuration. Copy to .env next to docker-compose.yml and adjust.

# Image version for web, migrate and api. Defaults to "latest".
# For production, pin an exact release so upgrades are deliberate, e.g. MEGOOFLAGS_VERSION=0.1.0
MEGOOFLAGS_VERSION=latest

# TLS mode, chosen by this one value:
# - Empty (default): plain HTTP on port 80 only, for running behind your own reverse proxy or load
#   balancer (Nginx, Traefik, Cloudflare, Coolify, ...). Caddy never requests certificates and port
#   443 is not published. Leave it empty; ":80" also counts as a domain here and publishes 443.
# - A domain, e.g. flags.example.com: automatic HTTPS. Caddy gets and renews a Let's Encrypt
#   certificate (kept in the caddy_data volume), serves ports 80 and 443 and redirects HTTP to HTTPS.
#   The domain's DNS must point at this host and ports 80 and 443 must be reachable from the internet.
MEGOOFLAGS_SITE_ADDRESS=

# Host port for plain HTTP. With a domain, keep 80: the ACME HTTP-01 challenge needs it
# (only TLS-ALPN on 443 still works otherwise).
MEGOOFLAGS_HTTP_PORT=80

# Proxies whose X-Forwarded-* headers Caddy passes on to the API (Caddy syntax).
# Default: private IP ranges. Add your upstream proxy's address if it is public.
MEGOOFLAGS_TRUSTED_PROXIES=private_ranges

# External URL used for links, webhooks and SDK snippets, e.g. https://flags.example.com.
# Leave empty to derive it from each request's host and scheme.
MEGOOFLAGS_PUBLIC_URL=

# API log level (DEBUG, INFO, WARNING, ERROR). Logs are JSON lines on stdout.
MEGOOFLAGS_LOG_LEVEL=INFO

# OpenTelemetry traces. Off unless an OTLP/HTTP endpoint is set, e.g. http://otel-collector:4318
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_SERVICE_NAME=megooflags-api
# Fraction of traces to sample, 0.0 to 1.0.
OTEL_TRACES_SAMPLER_ARG=1.0

# Prometheus metrics are served on :8000/metrics by every api container, inside the compose
# network only (Caddy answers 404). Attach Prometheus to the "megooflags_default" network and
# scrape all replicas with:
#   scrape_configs:
#     - job_name: megooflags-api
#       dns_sd_configs:
#         - names: [api]
#           type: A
#           port: 8000

# Database. Change the password and keep both lines in sync.
POSTGRES_PASSWORD=megooflags
MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://megooflags:megooflags@db:5432/megooflags
```

## Appendix H: `scripts/smoke.sh`
```bash
#!/usr/bin/env bash
# Compose smoke test: start the stack from locally built megooflags-{api,web}:ci images and check it
# through Caddy. Mode "http" (default): plain HTTP with two api replicas. Mode "https": automatic HTTPS
# for localhost, with a certificate from Caddy's internal CA.
set -euo pipefail
mode="${1:-http}"
case "$mode" in
  http) export MEGOOFLAGS_SITE_ADDRESS="" replicas=2 ;;
  https) export MEGOOFLAGS_SITE_ADDRESS="localhost" replicas=1 ;;
  *) echo "usage: $0 [http|https]" >&2; exit 2 ;;
esac
root="$(cd "$(dirname "$0")/.." && pwd)"
export MEGOOFLAGS_HTTP_PORT="${MEGOOFLAGS_HTTP_PORT:-8080}"
export MEGOOFLAGS_HTTPS_PORT="${MEGOOFLAGS_HTTPS_PORT:-8443}"
compose=(docker compose -p megooflags-smoke -f "$root/deploy/docker-compose.yml" -f "$root/deploy/compose.ci.yml")
workdir="$(mktemp -d)"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    "${compose[@]}" ps -a || true
    "${compose[@]}" logs --no-color || true
  fi
  "${compose[@]}" down -v --remove-orphans || true
  rm -rf "$workdir"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

"${compose[@]}" up -d --wait --wait-timeout 180 --scale "api=$replicas" caddy

if [ "$mode" = https ]; then
  for _ in $(seq 30); do
    "${compose[@]}" cp caddy:/data/caddy/pki/authorities/local/root.crt "$workdir/root.crt" 2>/dev/null && break
    sleep 2
  done
  [ -s "$workdir/root.crt" ] || fail "Caddy's local CA root was not created"
  base="https://localhost:${MEGOOFLAGS_HTTPS_PORT}"
  tls=(--cacert "$workdir/root.crt")
else
  base="http://localhost:${MEGOOFLAGS_HTTP_PORT}"
  tls=()
fi

check() {
  local path=$1 expected=$2 body
  body=$(curl -fsS "${tls[@]}" --retry 10 --retry-delay 2 --retry-all-errors "${base}${path}")
  grep -q -- "$expected" <<<"$body" || fail "GET $path: expected '$expected', got: $body"
  echo "GET $path OK"
}

check /healthz '"status":"ok"'
check /readyz '"status":"ready"'
check / 'MegooFlags'

code=$(curl -sS "${tls[@]}" -o /dev/null -w '%{http_code}' "${base}/metrics")
[ "$code" = 404 ] || fail "/metrics through Caddy returned $code, expected 404"
echo "GET /metrics via Caddy is 404 OK"

# Spread requests over the replicas; /api/* 404s are access-logged at INFO with route "other".
for _ in $(seq 10); do
  curl -fsS "${tls[@]}" -o /dev/null "${base}/healthz"
  curl -sS "${tls[@]}" -o /dev/null "${base}/api/smoke"
done
echo "repeated requests OK"

"${compose[@]}" exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=5).read().decode())" \
  | grep -q '^megooflags_http_requests_total' || fail "api /metrics has no megooflags_http_requests_total"
echo "internal /metrics OK"

ids=$("${compose[@]}" ps -q api)
[ "$(wc -w <<<"$ids")" -eq "$replicas" ] || fail "expected $replicas api replicas, got: $ids"
for id in $ids; do
  docker logs "$id" 2>&1 | grep -q '"route": "other"' || fail "api container $id has no JSON access-log line"
done
echo "JSON access logs on $replicas replica(s) OK"

if [ "$mode" = https ]; then
  redirect=$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' "http://localhost:${MEGOOFLAGS_HTTP_PORT}/")
  [[ "$redirect" =~ ^30[178]\ https:// ]] || fail "HTTP did not redirect to HTTPS: $redirect"
  echo "HTTP -> HTTPS redirect OK"
else
  if curl -sSk --max-time 5 -o /dev/null "https://localhost:${MEGOOFLAGS_HTTPS_PORT}/healthz" 2>/dev/null; then
    fail "something answers HTTPS on ${MEGOOFLAGS_HTTPS_PORT} in plain-HTTP mode"
  fi
  echo "no HTTPS listener OK"
fi

# Idle footprint: keep the whole stack under 1 GiB so a 2 GB VM has headroom.
mem_mib=$(docker stats --no-stream --format '{{.MemUsage}}' $("${compose[@]}" ps -q) | awk '
  { v = $1; u = v; gsub(/[0-9.]/, "", u); sub(/[A-Za-z]+$/, "", v)
    if (u == "GiB") v *= 1024; else if (u == "KiB") v /= 1024; else if (u == "B") v /= 1048576
    total += v }
  END { printf "%d", total }')
echo "idle memory: ${mem_mib} MiB"
[ "$mem_mib" -lt 1024 ] || fail "stack uses ${mem_mib} MiB, limit 1024 MiB"

echo "smoke OK ($mode)"
```

## Appendix I: `.github/workflows/ci.yml` smoke job
```yaml
  smoke:
    runs-on: ubuntu-24.04
    needs: images
    strategy:
      fail-fast: false
      matrix:
        mode: [http, https]
    steps:
      # ... checkout, version and the two build-push-action steps stay unchanged ...
      - run: bash scripts/smoke.sh ${{ matrix.mode }}
```

## Appendix J: CHANGELOG `[Unreleased]`
```markdown
## [Unreleased]

### Changed
- **Upgrade step:** `MEGOOFLAGS_SITE_ADDRESS` now defaults to empty, which means plain HTTP on port 80 only. Caddy publishes port 443 only when it is set to a domain. If your `.env` has `MEGOOFLAGS_SITE_ADDRESS=:80`, change it to `MEGOOFLAGS_SITE_ADDRESS=`, otherwise port 443 is still published on the host.
- The API trusts `X-Forwarded-*` headers only from private networks (configurable with `FORWARDED_ALLOW_IPS`), not from any address.
- API logs are JSON lines on stdout, with one access-log line per request. Set `MEGOOFLAGS_LOG_LEVEL` to change the level.

### Added
- Prometheus metrics on `:8000/metrics` in every `api` container, reachable only inside the compose network. `.env.example` shows a scrape config.
- OpenTelemetry traces for API requests and database queries, enabled by setting `OTEL_EXPORTER_OTLP_ENDPOINT`.
- `MEGOOFLAGS_PUBLIC_URL` sets the external URL. When it is empty, the URL is derived from the request.
- Caddy load-balances across API replicas started with `docker compose up -d --scale api=N`.
```

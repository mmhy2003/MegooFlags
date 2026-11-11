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

# The stream is long-lived, and client keys may travel in its `?key=` query string; invitation
# links carry their token in the path, and SSO callbacks an authorization code in the query.
EXCLUDED_URLS = "/healthz,/readyz,/metrics,/sdk/v1/stream,/api/invitations,/api/auth/sso"

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

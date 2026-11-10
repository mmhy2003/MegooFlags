import httpx
import pytest
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from megooflags_api.main import create_app
from megooflags_api.tracing import EXCLUDED_URLS, configure_tracing


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


def test_stream_is_not_traced() -> None:
    # The instrumentation records the full URL, and client keys may travel in `?key=`.
    assert "/sdk/v1/stream" in EXCLUDED_URLS.split(",")

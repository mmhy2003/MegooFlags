import json
import logging
import logging.config
from pathlib import Path

import httpx
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider

from megooflags_api.logging import JsonFormatter, access_logger
from megooflags_api.main import create_app
from megooflags_api.settings import Settings

API_DIR = Path(__file__).resolve().parents[1]


def record(msg: str = "hello %s", **extra: object) -> logging.LogRecord:
    rec = logging.LogRecord(
        "megooflags_api.test", logging.WARNING, __file__, 1, msg, ("world",), None
    )
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
    create_app(Settings(log_format="text"))
    assert not isinstance(logging.getLogger().handlers[0].formatter, JsonFormatter)
    create_app(Settings())  # restore JSON for the rest of the session


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, rec: logging.LogRecord) -> None:
        self.records.append(rec)


def reaching_root() -> list[str]:
    capture = Capture()
    logging.getLogger().addHandler(capture)
    try:
        logging.getLogger("uvicorn.access").info("GET /healthz 200")
        logging.getLogger("uvicorn.error").info("Uvicorn running on")
    finally:
        logging.getLogger().removeHandler(capture)
    return [rec.name for rec in capture.records]


def test_uvicorn_access_log_stays_muted() -> None:
    # AccessLogMiddleware owns access logging. Neither the --log-config file nor create_app
    # (which runs after uvicorn applied --no-access-log) may route uvicorn.access to stdout.
    config = json.loads((API_DIR / "log_config.json").read_text(encoding="utf-8"))
    logging.config.dictConfig(config)
    assert reaching_root() == ["uvicorn.error"]
    create_app(Settings())
    assert reaching_root() == ["uvicorn.error"]


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


async def test_query_key_not_logged() -> None:
    app = create_app(Settings())
    capture = Capture()
    logging.getLogger().addHandler(capture)
    access_logger.addHandler(capture)
    access_logger.setLevel(logging.DEBUG)
    key = "mf_client_xsecretthatmustnotleak"  # not a valid key shape: 401 without a DB lookup
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/sdk/v1/stream", params={"key": key})
    finally:
        logging.getLogger().removeHandler(capture)
        access_logger.removeHandler(capture)
        access_logger.setLevel(logging.NOTSET)
        await app.state.valkey.aclose()
        await app.state.engine.dispose()

    assert r.status_code == 401
    server = [rec for rec in capture.records if not rec.name.startswith("httpx")]  # the client
    assert any(rec.name == access_logger.name for rec in server)
    for rec in server:
        assert key not in json.dumps(rec.__dict__, default=str)


async def test_credentials_not_logged(app: FastAPI) -> None:
    secrets = ("mf_api_SECRETTOKENTHATMUSTNOTLEAK", "SECRETCOOKIE", "mf_client_SECRETKEYNOTLEAK")
    capture = Capture()
    access_logger.addHandler(capture)
    access_logger.setLevel(logging.DEBUG)
    headers = {"Authorization": f"Bearer {secrets[0]}", "Cookie": f"mf_session={secrets[1]}"}
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for path in ("/sdk/v1/stream", "/api/projects"):
                r = await client.get(path, params={"key": secrets[2]}, headers=headers)
                assert r.status_code == 401
    finally:
        access_logger.removeHandler(capture)
        access_logger.setLevel(logging.NOTSET)

    assert [rec.getMessage() for rec in capture.records] == [
        "GET /sdk/v1/stream 401",
        "GET /api/projects 401",
    ]
    for rec in capture.records:
        logged = rec.getMessage() + json.dumps(rec.__dict__, default=str)
        for secret in secrets:
            assert secret not in logged

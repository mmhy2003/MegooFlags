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
            # uvicorn.access stays muted: AccessLogMiddleware logs requests instead, and this
            # config is re-applied after uvicorn's --no-access-log, so it must not re-enable it.
            name: {"level": level, "handlers": [], "propagate": name != "uvicorn.access"}
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

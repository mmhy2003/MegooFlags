import logging

import pytest
from test_logging import Capture

from megooflags_api.main import create_app
from megooflags_api.settings import Settings
from megooflags_api.valkey import redacted_url

NEW_SETTINGS = (
    "MEGOOFLAGS_VALKEY_URL",
    "MEGOOFLAGS_PERSIST_CONTEXT",
    "MEGOOFLAGS_EXPOSURE_RETENTION_DAYS",
    "MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE",
    "MEGOOFLAGS_SSE_HEARTBEAT_SECONDS",
    "MEGOOFLAGS_STREAM_RECHECK_SECONDS",
)


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NEW_SETTINGS:
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.valkey_url == "redis://localhost:6379/0"
    assert settings.persist_context is False
    assert settings.exposure_retention_days == 30
    assert settings.client_rate_limit_per_minute == 600
    assert settings.sse_heartbeat_seconds == 15
    assert settings.stream_recheck_seconds == 30


def test_redacted_url() -> None:
    assert redacted_url("redis://user:secret@h:6379/0") == "redis://h:6379/0"


async def test_startup_fails_without_valkey() -> None:
    app = create_app(Settings(valkey_url="redis://:secret@127.0.0.1:1/0"))
    # create_app runs dictConfig, which resets root handlers, so caplog would miss the record.
    capture = Capture()
    logger = logging.getLogger("megooflags_api.valkey")
    logger.addHandler(capture)
    try:
        with pytest.raises(RuntimeError, match="cannot reach Valkey"):
            async with app.router.lifespan_context(app):
                pass
    finally:
        logger.removeHandler(capture)
        await app.state.valkey.aclose()
        await app.state.engine.dispose()

    errors = [r for r in capture.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "127.0.0.1:1" in message
    assert "secret" not in message


async def test_startup_with_valkey(valkey_up: None) -> None:
    app = create_app(Settings())
    async with app.router.lifespan_context(app):
        pass


def test_create_app_needs_no_valkey() -> None:
    app = create_app(Settings(valkey_url="redis://127.0.0.1:1/0"))
    assert app.openapi()["info"]["title"] == "MegooFlags API"

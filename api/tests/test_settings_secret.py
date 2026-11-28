import pytest
from pydantic import ValidationError

from megooflags_api.crypto import SecretBox, require_secret_key
from megooflags_api.main import create_app
from megooflags_api.settings import Settings


def test_short_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(secret_key="too-short")


def test_empty_key_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # docker-compose passes MEGOOFLAGS_SECRET_KEY="" when it is not configured.
    monkeypatch.setenv("MEGOOFLAGS_SECRET_KEY", "")

    assert Settings().secret_key is None


def test_require_secret_key_without_key() -> None:
    with pytest.raises(RuntimeError, match="MEGOOFLAGS_SECRET_KEY"):
        require_secret_key(Settings(secret_key=None))


def test_require_secret_key_with_key() -> None:
    box = require_secret_key(Settings(secret_key="k" * 32))

    assert box.decrypt(SecretBox("k" * 32).encrypt("x")) == "x"


async def test_create_app_has_no_secret_box_without_key() -> None:
    app = create_app(Settings(secret_key=None))
    try:
        assert app.state.secret_box is None
        with pytest.raises(RuntimeError, match="MEGOOFLAGS_SECRET_KEY"):
            async with app.router.lifespan_context(app):
                pass
    finally:
        await app.state.valkey.aclose()
        await app.state.engine.dispose()


async def test_create_app_has_secret_box_with_key() -> None:
    app = create_app(Settings(secret_key="k" * 32))
    try:
        box = app.state.secret_box
        assert box.decrypt(SecretBox("k" * 32).encrypt("x")) == "x"
    finally:
        await app.state.valkey.aclose()
        await app.state.engine.dispose()

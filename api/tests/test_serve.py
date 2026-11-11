import os
from pathlib import Path
from typing import Any

import pytest

from megooflags_api import serve

KEY = "k" * 32


def test_worker_count() -> None:
    assert serve.worker_count(0, 16) == serve.MAX_AUTO_WORKERS
    assert serve.worker_count(0, 2) == 2
    assert serve.worker_count(0, None) == 1
    assert serve.worker_count(3, 16) == 3
    assert serve.worker_count(12, 2) == 12  # an explicit count is not capped


class FakeUvicorn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, app: str, **options: Any) -> None:
        self.calls.append((app, options))


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeUvicorn:
    fake = FakeUvicorn()
    monkeypatch.setattr(serve.uvicorn, "run", fake)
    return fake


def test_refuses_to_start_without_secret_key(
    fake_run: FakeUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_check_valkey(url: str) -> None:
        pass

    monkeypatch.setattr(serve, "_check_valkey", fake_check_valkey)

    with pytest.raises(SystemExit) as exc:
        serve.main({"MEGOOFLAGS_SECRET_KEY": ""})

    assert exc.value.code == 1
    assert fake_run.calls == []


def test_refuses_to_start_without_valkey(fake_run: FakeUvicorn) -> None:
    environ = {"MEGOOFLAGS_VALKEY_URL": "redis://127.0.0.1:1/0", "MEGOOFLAGS_SECRET_KEY": KEY}

    with pytest.raises(SystemExit) as exc:
        serve.main(environ)

    assert exc.value.code == 1
    assert fake_run.calls == []


@pytest.mark.usefixtures("valkey_up")
def test_several_workers_share_metrics(fake_run: FakeUvicorn) -> None:
    environ = {
        "MEGOOFLAGS_VALKEY_URL": os.environ["MEGOOFLAGS_VALKEY_URL"],
        "MEGOOFLAGS_API_WORKERS": "3",
        "MEGOOFLAGS_SECRET_KEY": KEY,
    }

    serve.main(environ)

    [(app, options)] = fake_run.calls
    assert app == "megooflags_api.main:app"
    assert options["workers"] == 3
    assert options["host"] == "0.0.0.0"
    assert options["port"] == 8000
    assert options["proxy_headers"] is True
    assert options["access_log"] is False
    assert options["timeout_graceful_shutdown"] == 5
    metrics_dir = Path(environ["PROMETHEUS_MULTIPROC_DIR"])
    assert metrics_dir.is_dir()
    assert list(metrics_dir.iterdir()) == []
    metrics_dir.rmdir()


@pytest.mark.usefixtures("valkey_up")
def test_one_worker_keeps_plain_metrics(fake_run: FakeUvicorn) -> None:
    environ = {
        "MEGOOFLAGS_VALKEY_URL": os.environ["MEGOOFLAGS_VALKEY_URL"],
        "MEGOOFLAGS_API_WORKERS": "1",
        "MEGOOFLAGS_SECRET_KEY": KEY,
    }

    serve.main(environ)

    [(_, options)] = fake_run.calls
    assert options["workers"] == 1
    assert "PROMETHEUS_MULTIPROC_DIR" not in environ

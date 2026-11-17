from pathlib import Path

import pytest
from pydantic import ValidationError

from megooflags_api.services.cluster.config import NODE_TOKEN_PREFIX, worker_config_errors
from megooflags_api.settings import Settings

URL_MISSING = (
    "MEGOOFLAGS_CONTROL_PLANE_URL is not set: set it to the control plane's URL, "
    "e.g. https://flags.example.com"
)
TOKEN_MISSING = (
    "MEGOOFLAGS_NODE_TOKEN is not set: create the worker on the Nodes page and copy its token"
)
TOKEN = NODE_TOKEN_PREFIX + "abc123"


def worker(**values: object) -> Settings:
    return Settings(role="worker", **values)  # type: ignore[arg-type]


def test_role_defaults_to_standalone() -> None:
    assert Settings().role == "standalone"


def test_role_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        Settings(role="edge")  # type: ignore[arg-type]


def test_empty_control_plane_url_and_token_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # docker-compose passes an empty string for a variable that is not configured.
    monkeypatch.setenv("MEGOOFLAGS_CONTROL_PLANE_URL", "")
    monkeypatch.setenv("MEGOOFLAGS_NODE_TOKEN", "")

    settings = Settings()

    assert settings.control_plane_url is None
    assert settings.node_token is None


def test_worker_defaults() -> None:
    settings = Settings()

    assert settings.data_dir == Path("/var/lib/megooflags")
    assert settings.worker_buffer_max_mb == 64
    assert settings.worker_drain_grace_seconds == 30
    assert settings.control_plane_insecure is False
    assert settings.control_plane_ca_file == ""


def test_worker_config_errors_lists_missing_settings() -> None:
    assert worker_config_errors(worker()) == [URL_MISSING, TOKEN_MISSING]


def test_worker_config_errors_refuses_http_unless_insecure() -> None:
    plain = worker(control_plane_url="http://cp.internal:8000", node_token=TOKEN)

    assert worker_config_errors(plain) == [
        "MEGOOFLAGS_CONTROL_PLANE_URL must use https://; "
        "set MEGOOFLAGS_CONTROL_PLANE_INSECURE=true to allow http on a private network"
    ]
    plain_allowed = worker(
        control_plane_url="http://cp.internal:8000", node_token=TOKEN, control_plane_insecure=True
    )
    assert worker_config_errors(plain_allowed) == []


def test_worker_config_errors_rejects_non_node_token() -> None:
    settings = worker(control_plane_url="https://flags.example.com", node_token="mf_server_abc")

    assert worker_config_errors(settings) == [
        "MEGOOFLAGS_NODE_TOKEN is not a node token (it starts with mf_node_)"
    ]


def test_worker_config_errors_missing_ca_file(tmp_path: Path) -> None:
    missing = tmp_path / "ca.pem"
    settings = worker(
        control_plane_url="https://flags.example.com",
        node_token=TOKEN,
        control_plane_ca_file=str(missing),
    )

    assert worker_config_errors(settings) == [
        f"MEGOOFLAGS_CONTROL_PLANE_CA_FILE {missing} does not exist"
    ]


def test_worker_config_ok(tmp_path: Path) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("-----BEGIN CERTIFICATE-----\n")
    settings = worker(
        control_plane_url="https://flags.example.com",
        node_token=TOKEN,
        control_plane_ca_file=str(ca_file),
    )

    assert worker_config_errors(settings) == []

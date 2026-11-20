"""Render tests for the Helm chart in deploy/helm/megooflags (helm template, helm lint)."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_compose_files import _api_environment

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "helm" / "megooflags"
SECRET = "s" * 32
REQUIRE = bool(os.environ.get("MEGOOFLAGS_REQUIRE_HELM"))
pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None and not REQUIRE, reason="helm is not installed"
)

WORKER = {
    "role": "worker",
    "worker": {"controlPlaneUrl": "https://cp.example.com", "nodeTokens": ["mf_node_a"]},
}
COMPOSE_FILES = {
    "standalone": "docker-compose.yml",
    "control-plane": "docker-compose.control-plane.yml",
    "worker": "docker-compose.worker.yml",
}
NOT_IN_CONFIG = {
    "MEGOOFLAGS_SECRET_KEY",
    "MEGOOFLAGS_DATABASE_URL",
    "MEGOOFLAGS_VALKEY_URL",
    "MEGOOFLAGS_NODE_TOKEN",
    "MEGOOFLAGS_CONTROL_PLANE_URL",
    "MEGOOFLAGS_CONTROL_PLANE_CA_FILE",
}
CONTROL_PLANE_ONLY = {
    "MEGOOFLAGS_PUBLIC_URL",
    "MEGOOFLAGS_PERSIST_CONTEXT",
    "MEGOOFLAGS_EXPOSURE_RETENTION_DAYS",
    "MEGOOFLAGS_STREAM_RECHECK_SECONDS",
    "MEGOOFLAGS_API_WORKERS",
    "MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE",
    "MEGOOFLAGS_WEBHOOK_TIMEOUT_SECONDS",
    "MEGOOFLAGS_WEBHOOK_RETENTION_DAYS",
    "MEGOOFLAGS_UPDATE_CHECK",
}


def _helm(values: dict[str, Any], tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "values.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return subprocess.run(
        ["helm", "template", "mf", str(CHART), "--namespace", "mf", "-f", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def render(tmp_path: Path, values: dict[str, Any]) -> list[dict[str, Any]]:
    result = _helm(values, tmp_path)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]


def render_error(tmp_path: Path, values: dict[str, Any]) -> str:
    result = _helm(values, tmp_path)
    assert result.returncode != 0, result.stdout
    return result.stderr


def by_kind(docs: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(doc["kind"], doc["metadata"]["name"]) for doc in docs}


def find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for doc in docs:
        if doc["kind"] == kind and doc["metadata"]["name"] == name:
            return doc
    raise AssertionError(f"{kind} {name} not rendered: {sorted(by_kind(docs))}")


def caddy_paths(name: str) -> set[tuple[str, str]]:
    text = (ROOT / "deploy" / name).read_text(encoding="utf-8")
    match = re.search(r"^\s*@api path (.+)$", text, re.M)
    assert match, f"no @api path line in {name}"
    paths: set[tuple[str, str]] = set()
    for path in match.group(1).split():
        if path.endswith("/*"):
            paths.add(("Prefix", path[:-2]))
        else:
            paths.add(("Exact", path))
    return paths


def compose_api_env(name: str) -> dict[str, str]:
    text = (ROOT / "deploy" / name).read_text(encoding="utf-8")
    env: dict[str, str] = {}
    for line in _api_environment(text):
        key, _, value = line.partition(": ")
        match = re.fullmatch(r"\$\{(\w+):-(.*)\}", value)
        env[key] = match.group(2) if match else value
    return env


def _config(docs: list[dict[str, Any]]) -> dict[str, str]:
    data: dict[str, str] = find(docs, "ConfigMap", "mf-megooflags-config")["data"]
    return data


def _secret(docs: list[dict[str, Any]]) -> dict[str, str]:
    data: dict[str, str] = find(docs, "Secret", "mf-megooflags")["stringData"]
    return data


def _role_values(role: str) -> dict[str, Any]:
    if role == "worker":
        return WORKER
    return {"role": role, "secretKey": SECRET}


@pytest.mark.parametrize(
    ("values", "message"),
    [
        pytest.param(
            {"role": "standalone"},
            "secretKey is required (32+ characters, e.g. openssl rand -hex 32), "
            "or set existingSecret",
            id="secret-key-missing",
        ),
        pytest.param(
            {"secretKey": "short"},
            "secretKey must be at least 32 characters",
            id="secret-key-short",
        ),
        pytest.param(
            {"secretKey": SECRET, "postgresql": {"enabled": False}},
            "externalDatabase.url is required when postgresql.enabled is false",
            id="database-url-missing",
        ),
        pytest.param(
            {
                "secretKey": SECRET,
                "postgresql": {"enabled": False},
                "externalDatabase": {"url": "postgresql://u:p@db:5432/x"},
            },
            "externalDatabase.url must use the postgresql+asyncpg:// driver",
            id="database-url-driver",
        ),
        pytest.param(
            {"secretKey": SECRET, "valkey": {"enabled": False}},
            "externalValkey.url is required when valkey.enabled is false",
            id="valkey-url-missing",
        ),
        pytest.param(
            {"role": "worker", "worker": {"nodeTokens": ["mf_node_a"]}},
            "worker.controlPlaneUrl is required when role is worker",
            id="control-plane-url-missing",
        ),
        pytest.param(
            {"role": "worker", "worker": {"controlPlaneUrl": "https://cp.example.com"}},
            "worker.nodeTokens is required when role is worker (one token per replica, "
            "from the Nodes page), or set existingSecret and worker.replicas",
            id="node-tokens-missing",
        ),
        pytest.param(
            {
                "role": "worker",
                "existingSecret": "tokens",
                "worker": {"controlPlaneUrl": "https://cp.example.com"},
            },
            "worker.nodeTokens is required when role is worker (one token per replica, "
            "from the Nodes page), or set existingSecret and worker.replicas",
            id="existing-secret-without-replicas",
        ),
    ],
)
def test_required_values(tmp_path: Path, values: dict[str, Any], message: str) -> None:
    assert message in render_error(tmp_path, values)


def test_unknown_role_fails(tmp_path: Path) -> None:
    assert "role" in render_error(tmp_path, {"role": "edge", "secretKey": SECRET})


@pytest.mark.parametrize("role", ["standalone", "control-plane", "worker"])
def test_config_defaults_match_compose(tmp_path: Path, role: str) -> None:
    config = _config(render(tmp_path, _role_values(role)))
    compose = compose_api_env(COMPOSE_FILES[role])
    for key, default in compose.items():
        if key in NOT_IN_CONFIG:
            continue
        if key == "MEGOOFLAGS_API_WORKERS":
            assert config[key] == "2"
        elif key == "MEGOOFLAGS_ROLE":
            assert config[key] == role
        else:
            assert config.get(key) == default, key
    service = "megooflags-worker" if role == "worker" else "megooflags-api"
    assert config["OTEL_SERVICE_NAME"] == service


def test_worker_config_has_no_control_plane_keys(tmp_path: Path) -> None:
    config = _config(render(tmp_path, WORKER))
    assert CONTROL_PLANE_ONLY.isdisjoint(config)
    assert config["MEGOOFLAGS_CONTROL_PLANE_URL"] == "https://cp.example.com"
    assert "MEGOOFLAGS_CONTROL_PLANE_CA_FILE" not in config


def test_control_plane_config_has_no_worker_keys(tmp_path: Path) -> None:
    config = _config(render(tmp_path, _role_values("control-plane")))
    for key in (
        "MEGOOFLAGS_WORKER_BUFFER_MAX_MB",
        "MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS",
        "MEGOOFLAGS_CONTROL_PLANE_URL",
        "MEGOOFLAGS_CONTROL_PLANE_INSECURE",
    ):
        assert key not in config


def test_worker_ca_file(tmp_path: Path) -> None:
    values = {**WORKER, "worker": {**WORKER["worker"], "controlPlaneCa": {"secret": "cp-ca"}}}
    config = _config(render(tmp_path, values))
    assert config["MEGOOFLAGS_CONTROL_PLANE_CA_FILE"] == "/etc/megooflags/certs/ca.crt"


def test_secret_keys_standalone(tmp_path: Path) -> None:
    docs = render(tmp_path, {"secretKey": SECRET, "postgresql": {"password": "p@ss"}})
    secret = find(docs, "Secret", "mf-megooflags")
    assert secret["type"] == "Opaque"
    assert secret["stringData"] == {
        "MEGOOFLAGS_SECRET_KEY": SECRET,
        "MEGOOFLAGS_DATABASE_URL": (
            "postgresql+asyncpg://megooflags:p%40ss@mf-megooflags-postgresql:5432/megooflags"
        ),
        "MEGOOFLAGS_VALKEY_URL": "redis://mf-megooflags-valkey:6379/0",
        "POSTGRES_PASSWORD": "p@ss",
    }


def test_existing_secret_renders_no_secret(tmp_path: Path) -> None:
    docs = render(tmp_path, {"existingSecret": "mine"})
    assert find(docs, "ConfigMap", "mf-megooflags-config")
    assert not [doc for doc in docs if doc["kind"] == "Secret"]


def test_external_urls_used(tmp_path: Path) -> None:
    database = "postgresql+asyncpg://u:p@db.example.com:5432/flags"
    valkey = "redis://cache.example.com:6379/1"
    values = {
        "secretKey": SECRET,
        "postgresql": {"enabled": False},
        "externalDatabase": {"url": database},
        "valkey": {"enabled": False},
        "externalValkey": {"url": valkey},
    }
    assert _secret(render(tmp_path, values)) == {
        "MEGOOFLAGS_SECRET_KEY": SECRET,
        "MEGOOFLAGS_DATABASE_URL": database,
        "MEGOOFLAGS_VALKEY_URL": valkey,
    }


def test_worker_secret_has_one_token_per_replica(tmp_path: Path) -> None:
    values = {**WORKER, "worker": {**WORKER["worker"], "nodeTokens": ["mf_node_a", "mf_node_b"]}}
    assert _secret(render(tmp_path, values)) == {"token-0": "mf_node_a", "token-1": "mf_node_b"}

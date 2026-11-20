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


CONTROL_PLANE_WORKLOADS = {
    ("ConfigMap", "mf-megooflags-config"),
    ("Secret", "mf-megooflags"),
    ("Deployment", "mf-megooflags-api"),
    ("Deployment", "mf-megooflags-web"),
    ("Service", "mf-megooflags-api"),
    ("Service", "mf-megooflags-web"),
    ("Job", "mf-megooflags-migrate-1"),
}
STORES = {
    ("StatefulSet", "mf-megooflags-postgresql"),
    ("Service", "mf-megooflags-postgresql"),
    ("Deployment", "mf-megooflags-valkey"),
    ("Service", "mf-megooflags-valkey"),
}
STORE_SECURITY = {"runAsNonRoot": True, "runAsUser": 999, "runAsGroup": 999, "fsGroup": 999}


def _app_version() -> str:
    match = re.search(r'^appVersion:\s*"?([^"\s]+)"?\s*$', (CHART / "Chart.yaml").read_text(), re.M)
    assert match
    return match.group(1)


def _pod(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    pod: dict[str, Any] = find(docs, kind, name)["spec"]["template"]
    return pod


def _container(pod: dict[str, Any], name: str, init: bool = False) -> dict[str, Any]:
    for container in pod["spec"]["initContainers" if init else "containers"]:
        if container["name"] == name:
            found: dict[str, Any] = container
            return found
    raise AssertionError(f"no container {name}")


def _command(container: dict[str, Any]) -> str:
    return " ".join(container["command"])


def test_standalone_resources(tmp_path: Path) -> None:
    assert by_kind(render(tmp_path, {"secretKey": SECRET})) == CONTROL_PLANE_WORKLOADS | STORES


def test_control_plane_resources(tmp_path: Path) -> None:
    docs = render(tmp_path, _role_values("control-plane"))
    assert by_kind(docs) == CONTROL_PLANE_WORKLOADS | STORES


def _env(container: dict[str, Any]) -> dict[str, Any]:
    return {item["name"]: item.get("value", item.get("valueFrom")) for item in container["env"]}


def test_postgresql_statefulset(tmp_path: Path) -> None:
    docs = render(tmp_path, {"secretKey": SECRET})
    statefulset = find(docs, "StatefulSet", "mf-megooflags-postgresql")
    assert statefulset["spec"]["serviceName"] == "mf-megooflags-postgresql"
    selector = statefulset["spec"]["selector"]["matchLabels"]
    assert selector["app.kubernetes.io/component"] == "postgresql"
    pod = statefulset["spec"]["template"]
    assert pod["metadata"]["labels"] == selector
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["securityContext"] == STORE_SECURITY
    postgres = _container(pod, "postgresql")
    assert postgres["image"] == "postgres:16"
    assert _env(postgres) == {
        "POSTGRES_USER": "megooflags",
        "POSTGRES_DB": "megooflags",
        "POSTGRES_PASSWORD": {
            "secretKeyRef": {"name": "mf-megooflags", "key": "POSTGRES_PASSWORD"}
        },
        "PGDATA": "/var/lib/postgresql/data/pgdata",
    }
    assert postgres["ports"] == [{"name": "postgresql", "containerPort": 5432, "protocol": "TCP"}]
    assert postgres["readinessProbe"]["exec"]["command"] == [
        "pg_isready",
        "-U",
        "megooflags",
        "-d",
        "megooflags",
    ]
    assert postgres["volumeMounts"] == [{"name": "data", "mountPath": "/var/lib/postgresql/data"}]
    assert postgres["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }
    assert "resources" not in postgres
    (claim,) = statefulset["spec"]["volumeClaimTemplates"]
    assert claim["metadata"]["name"] == "data"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert claim["spec"]["resources"] == {"requests": {"storage": "8Gi"}}
    assert "storageClassName" not in claim["spec"]
    service = find(docs, "Service", "mf-megooflags-postgresql")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"] == selector
    assert service["spec"]["ports"] == [
        {"name": "postgresql", "port": 5432, "targetPort": "postgresql", "protocol": "TCP"}
    ]


def test_postgresql_values(tmp_path: Path) -> None:
    values = {
        "existingSecret": "mine",
        "postgresql": {
            "image": "postgres:16.4",
            "persistence": {"size": "20Gi", "storageClass": "fast"},
            "resources": {"limits": {"memory": "1Gi"}},
        },
    }
    statefulset = find(render(tmp_path, values), "StatefulSet", "mf-megooflags-postgresql")
    postgres = _container(statefulset["spec"]["template"], "postgresql")
    assert postgres["image"] == "postgres:16.4"
    assert _env(postgres)["POSTGRES_PASSWORD"] == {
        "secretKeyRef": {"name": "mine", "key": "POSTGRES_PASSWORD"}
    }
    assert postgres["resources"] == {"limits": {"memory": "1Gi"}}
    (claim,) = statefulset["spec"]["volumeClaimTemplates"]
    assert claim["spec"]["storageClassName"] == "fast"
    assert claim["spec"]["resources"] == {"requests": {"storage": "20Gi"}}


def test_valkey_no_persistence(tmp_path: Path) -> None:
    docs = render(tmp_path, {"secretKey": SECRET})
    deployment = find(docs, "Deployment", "mf-megooflags-valkey")
    selector = deployment["spec"]["selector"]["matchLabels"]
    assert selector["app.kubernetes.io/component"] == "valkey"
    pod = deployment["spec"]["template"]
    assert pod["metadata"]["labels"] == selector
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["securityContext"] == STORE_SECURITY
    assert "volumes" not in pod["spec"]
    valkey = _container(pod, "valkey")
    assert valkey["image"] == "valkey/valkey:8"
    assert valkey["args"] == ["valkey-server", "--save", "", "--appendonly", "no"]
    assert "volumeMounts" not in valkey
    assert valkey["readinessProbe"]["exec"]["command"] == ["valkey-cli", "ping"]
    assert valkey["ports"] == [{"name": "valkey", "containerPort": 6379, "protocol": "TCP"}]
    assert valkey["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }
    service = find(docs, "Service", "mf-megooflags-valkey")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"] == selector
    assert service["spec"]["ports"] == [
        {"name": "valkey", "port": 6379, "targetPort": "valkey", "protocol": "TCP"}
    ]


def test_valkey_values(tmp_path: Path) -> None:
    values = {
        "secretKey": SECRET,
        "valkey": {"image": "valkey/valkey:8.1", "resources": {"limits": {"memory": "256Mi"}}},
    }
    pod = _pod(render(tmp_path, values), "Deployment", "mf-megooflags-valkey")
    valkey = _container(pod, "valkey")
    assert valkey["image"] == "valkey/valkey:8.1"
    assert valkey["resources"] == {"limits": {"memory": "256Mi"}}


def test_stores_disabled(tmp_path: Path) -> None:
    values = {
        "secretKey": SECRET,
        "postgresql": {"enabled": False},
        "externalDatabase": {"url": "postgresql+asyncpg://u:p@db.example.com:5432/flags"},
        "valkey": {"enabled": False},
        "externalValkey": {"url": "redis://cache.example.com:6379/0"},
    }
    assert by_kind(render(tmp_path, values)) == CONTROL_PLANE_WORKLOADS


def test_worker_has_no_stores(tmp_path: Path) -> None:
    assert not by_kind(render(tmp_path, WORKER)) & STORES


def test_image_tag_defaults_to_app_version(tmp_path: Path) -> None:
    docs = render(tmp_path, {"secretKey": SECRET})
    version = _app_version()
    api = _pod(docs, "Deployment", "mf-megooflags-api")
    web = _pod(docs, "Deployment", "mf-megooflags-web")
    job = _pod(docs, "Job", "mf-megooflags-migrate-1")
    assert _container(api, "api")["image"] == f"ghcr.io/mmhy2003/megooflags-api:{version}"
    assert _container(api, "wait-for-migrations", init=True)["image"] == (
        f"ghcr.io/mmhy2003/megooflags-api:{version}"
    )
    assert _container(job, "migrate")["image"] == f"ghcr.io/mmhy2003/megooflags-api:{version}"
    assert _container(web, "web")["image"] == f"ghcr.io/mmhy2003/megooflags-web:{version}"


def test_image_registry_empty(tmp_path: Path) -> None:
    values = {"secretKey": SECRET, "image": {"registry": "", "tag": "ci", "pullPolicy": "Never"}}
    docs = render(tmp_path, values)
    api = _container(_pod(docs, "Deployment", "mf-megooflags-api"), "api")
    web = _container(_pod(docs, "Deployment", "mf-megooflags-web"), "web")
    assert api["image"] == "megooflags-api:ci"
    assert web["image"] == "megooflags-web:ci"
    assert api["imagePullPolicy"] == web["imagePullPolicy"] == "Never"


def test_image_pull_secrets(tmp_path: Path) -> None:
    docs = render(tmp_path, {"secretKey": SECRET, "imagePullSecrets": [{"name": "ghcr"}]})
    for kind, name in [
        ("Deployment", "mf-megooflags-api"),
        ("Deployment", "mf-megooflags-web"),
        ("Job", "mf-megooflags-migrate-1"),
    ]:
        assert _pod(docs, kind, name)["spec"]["imagePullSecrets"] == [{"name": "ghcr"}]


def test_api_waits_for_migrations(tmp_path: Path) -> None:
    api = _pod(render(tmp_path, {"secretKey": SECRET}), "Deployment", "mf-megooflags-api")
    init = _container(api, "wait-for-migrations", init=True)
    assert "alembic current" in _command(init)
    assert "(head)" in _command(init)
    assert init["envFrom"] == [
        {"configMapRef": {"name": "mf-megooflags-config"}},
        {"secretRef": {"name": "mf-megooflags"}},
    ]
    assert _container(api, "api")["envFrom"] == init["envFrom"]


def test_migrate_job(tmp_path: Path) -> None:
    values = {"secretKey": SECRET, "migrate": {"annotations": {"argocd.argoproj.io/hook": "Sync"}}}
    job = find(render(tmp_path, values), "Job", "mf-megooflags-migrate-1")
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "Sync"
    assert job["spec"]["backoffLimit"] == 0
    pod = job["spec"]["template"]
    assert pod["spec"]["restartPolicy"] == "Never"
    migrate = _container(pod, "migrate")
    assert "alembic upgrade head" in _command(migrate)
    assert migrate["envFrom"][1] == {"secretRef": {"name": "mf-megooflags"}}


def _checksums(tmp_path: Path, values: dict[str, Any]) -> dict[str, str]:
    api = _pod(render(tmp_path, values), "Deployment", "mf-megooflags-api")
    annotations: dict[str, str] = api["metadata"]["annotations"]
    return annotations


def test_checksum_annotations_change(tmp_path: Path) -> None:
    before = _checksums(tmp_path, {"secretKey": SECRET})
    after = _checksums(tmp_path, {"secretKey": SECRET, "config": {"MEGOOFLAGS_LOG_LEVEL": "DEBUG"}})
    assert before["checksum/config"] != after["checksum/config"]
    assert before["checksum/secret"] == after["checksum/secret"]
    rotated = _checksums(tmp_path, {"secretKey": "t" * 32})
    assert rotated["checksum/secret"] != before["checksum/secret"]
    assert "checksum/secret" not in _checksums(tmp_path, {"existingSecret": "mine"})


def test_pod_annotations_merged(tmp_path: Path) -> None:
    values = {
        "secretKey": SECRET,
        "api": {"podAnnotations": {"example.com/a": "1"}},
        "web": {"podAnnotations": {"example.com/w": "2"}},
    }
    docs = render(tmp_path, values)
    api = _pod(docs, "Deployment", "mf-megooflags-api")["metadata"]["annotations"]
    web = _pod(docs, "Deployment", "mf-megooflags-web")["metadata"]["annotations"]
    assert api["example.com/a"] == "1"
    assert "checksum/config" in api
    assert web["example.com/w"] == "2"


def test_web_points_at_api_service(tmp_path: Path) -> None:
    web = _pod(render(tmp_path, {"secretKey": SECRET}), "Deployment", "mf-megooflags-web")
    env = {item["name"]: item.get("value") for item in _container(web, "web")["env"]}
    assert env["MEGOOFLAGS_API_URL"] == "http://mf-megooflags-api:8000"


def test_services_and_probes(tmp_path: Path) -> None:
    docs = render(tmp_path, {"secretKey": SECRET})
    for component, port in [("api", 8000), ("web", 3000)]:
        service = find(docs, "Service", f"mf-megooflags-{component}")
        assert service["spec"]["ports"] == [
            {"name": "http", "port": port, "targetPort": "http", "protocol": "TCP"}
        ]
        assert service["spec"]["selector"]["app.kubernetes.io/component"] == component
        deployment = find(docs, "Deployment", f"mf-megooflags-{component}")
        assert deployment["spec"]["strategy"] == {
            "type": "RollingUpdate",
            "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
        }
    api = _container(_pod(docs, "Deployment", "mf-megooflags-api"), "api")
    assert api["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert api["livenessProbe"]["periodSeconds"] == 10
    assert api["livenessProbe"]["failureThreshold"] == 3
    assert api["readinessProbe"]["httpGet"] == {"path": "/readyz", "port": "http"}
    assert api["readinessProbe"]["periodSeconds"] == 5
    web = _container(_pod(docs, "Deployment", "mf-megooflags-web"), "web")
    assert web["livenessProbe"]["tcpSocket"] == {"port": "http"}
    assert web["readinessProbe"]["tcpSocket"] == {"port": "http"}


def test_pods_non_root(tmp_path: Path) -> None:
    docs = render(tmp_path, {"secretKey": SECRET})
    for kind, name in [
        ("Deployment", "mf-megooflags-api"),
        ("Deployment", "mf-megooflags-web"),
        ("Job", "mf-megooflags-migrate-1"),
    ]:
        spec = _pod(docs, kind, name)["spec"]
        assert spec["automountServiceAccountToken"] is False
        assert spec["securityContext"] == {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "fsGroup": 10001,
        }
        for container in spec["containers"] + spec.get("initContainers", []):
            assert container["securityContext"] == {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
            }, (name, container["name"])


def test_pod_scheduling_and_extra_env(tmp_path: Path) -> None:
    affinity = {"nodeAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": []}}
    toleration = {"key": "dedicated", "operator": "Exists", "effect": "NoSchedule"}
    values = {
        "secretKey": SECRET,
        "api": {
            "extraEnv": [{"name": "EXTRA", "value": "1"}],
            "nodeSelector": {"pool": "flags"},
            "tolerations": [toleration],
            "affinity": affinity,
            "resources": {"limits": {"memory": "512Mi"}},
        },
        "web": {"extraEnv": [{"name": "WEB_EXTRA", "value": "2"}]},
        "migrate": {"resources": {"limits": {"memory": "256Mi"}}},
    }
    docs = render(tmp_path, values)
    api = _pod(docs, "Deployment", "mf-megooflags-api")["spec"]
    assert api["nodeSelector"] == {"pool": "flags"}
    assert api["tolerations"] == [toleration]
    assert api["affinity"] == affinity
    container = _container({"spec": api}, "api")
    assert {"name": "EXTRA", "value": "1"} in container["env"]
    assert container["resources"] == {"limits": {"memory": "512Mi"}}
    web = _pod(docs, "Deployment", "mf-megooflags-web")["spec"]
    assert "nodeSelector" not in web
    assert {"name": "WEB_EXTRA", "value": "2"} in _container({"spec": web}, "web")["env"]
    job = _pod(docs, "Job", "mf-megooflags-migrate-1")
    assert _container(job, "migrate")["resources"] == {"limits": {"memory": "256Mi"}}


def test_api_replicas(tmp_path: Path) -> None:
    docs = render(tmp_path, {"secretKey": SECRET, "api": {"replicas": 3}})
    assert find(docs, "Deployment", "mf-megooflags-api")["spec"]["replicas"] == 3
    assert find(docs, "Deployment", "mf-megooflags-web")["spec"]["replicas"] == 1


def test_worker_has_no_control_plane_workloads(tmp_path: Path) -> None:
    kinds = {kind for kind, _ in by_kind(render(tmp_path, WORKER))}
    assert not kinds & {"Deployment", "Job"}


WORKERS = {
    "role": "worker",
    "worker": {
        "controlPlaneUrl": "https://cp.example.com",
        "nodeTokens": ["mf_node_a", "mf_node_b"],
    },
}
WORKER_RESOURCES = {
    ("ConfigMap", "mf-megooflags-config"),
    ("Secret", "mf-megooflags"),
    ("StatefulSet", "mf-megooflags-worker"),
    ("Service", "mf-megooflags-worker"),
    ("Service", "mf-megooflags-worker-headless"),
}


def _workers(**worker: Any) -> dict[str, Any]:
    return {**WORKERS, "worker": {**WORKERS["worker"], **worker}}


def _worker_set(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return find(docs, "StatefulSet", "mf-megooflags-worker")


def test_worker_resources(tmp_path: Path) -> None:
    result = _helm(WORKERS, tmp_path)
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc is not None]
    assert by_kind(docs) == WORKER_RESOURCES
    words = ("mf-megooflags-web", "mf-megooflags-api", "kind: Job", "postgresql", "valkey")
    for word in (*words, "MEGOOFLAGS_SECRET_KEY"):
        assert word not in result.stdout


def test_worker_replicas_follow_tokens(tmp_path: Path) -> None:
    values = _workers(nodeTokens=["mf_node_a", "mf_node_b", "mf_node_c"])
    assert _worker_set(render(tmp_path, values))["spec"]["replicas"] == 3


def test_worker_existing_secret_replicas(tmp_path: Path) -> None:
    values = {**_workers(nodeTokens=[], replicas=4), "existingSecret": "tokens"}
    docs = render(tmp_path, values)
    assert _worker_set(docs)["spec"]["replicas"] == 4
    assert ("Secret", "mf-megooflags") not in by_kind(docs)


def test_worker_picks_token_by_ordinal(tmp_path: Path) -> None:
    for values, secret in [(WORKERS, "mf-megooflags"), ({**WORKERS, "existingSecret": "t"}, "t")]:
        pod = _worker_set(render(tmp_path, values))["spec"]["template"]
        worker = _container(pod, "worker")
        assert worker["image"] == f"ghcr.io/mmhy2003/megooflags-api:{_app_version()}"
        command = _command(worker)
        assert command.startswith("/bin/sh -c ")
        assert "token-${ordinal}" in command
        assert f"Secret {secret}:" in command
        assert "exec python -m megooflags_api.serve" in command
        assert worker["envFrom"] == [{"configMapRef": {"name": "mf-megooflags-config"}}]
        assert {"name": "tokens", "mountPath": "/etc/megooflags/tokens", "readOnly": True} in (
            worker["volumeMounts"]
        )
        assert {"name": "tokens", "secret": {"secretName": secret, "defaultMode": 288}} in (
            pod["spec"]["volumes"]
        )
        assert pod["spec"]["securityContext"]["fsGroup"] == 10001
        assert pod["spec"]["automountServiceAccountToken"] is False
        assert worker["securityContext"] == {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        }


def test_worker_volume(tmp_path: Path) -> None:
    statefulset = _worker_set(render(tmp_path, WORKERS))
    (claim,) = statefulset["spec"]["volumeClaimTemplates"]
    assert claim["metadata"]["name"] == "data"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert claim["spec"]["resources"] == {"requests": {"storage": "1Gi"}}
    assert "storageClassName" not in claim["spec"]
    worker = _container(statefulset["spec"]["template"], "worker")
    assert {"name": "data", "mountPath": "/var/lib/megooflags"} in worker["volumeMounts"]
    values = _workers(persistence={"size": "2Gi", "storageClass": "fast"})
    (claim,) = _worker_set(render(tmp_path, values))["spec"]["volumeClaimTemplates"]
    assert claim["spec"]["storageClassName"] == "fast"
    assert claim["spec"]["resources"] == {"requests": {"storage": "2Gi"}}


@pytest.mark.parametrize(
    ("source", "volume"),
    [
        ("configMap", {"configMap": {"name": "cp-ca"}}),
        ("secret", {"secret": {"secretName": "cp-ca"}}),
    ],
)
def test_worker_ca_mount(tmp_path: Path, source: str, volume: dict[str, Any]) -> None:
    docs = render(tmp_path, _workers(controlPlaneCa={source: "cp-ca"}))
    pod = _worker_set(docs)["spec"]["template"]
    assert {"name": "ca", **volume} in pod["spec"]["volumes"]
    mount = {"name": "ca", "mountPath": "/etc/megooflags/certs", "readOnly": True}
    assert mount in _container(pod, "worker")["volumeMounts"]
    assert _config(docs)["MEGOOFLAGS_CONTROL_PLANE_CA_FILE"] == "/etc/megooflags/certs/ca.crt"
    plain = _worker_set(render(tmp_path, WORKERS))["spec"]["template"]
    assert [v["name"] for v in plain["spec"]["volumes"]] == ["tokens"]


def test_worker_insecure_flag(tmp_path: Path) -> None:
    docs = render(tmp_path, _workers(controlPlaneInsecure=True))
    assert _config(docs)["MEGOOFLAGS_CONTROL_PLANE_INSECURE"] == "true"


def test_worker_parallel_pods(tmp_path: Path) -> None:
    docs = render(tmp_path, WORKERS)
    spec = _worker_set(docs)["spec"]
    assert spec["podManagementPolicy"] == "Parallel"
    assert spec["updateStrategy"] == {"type": "RollingUpdate"}
    assert spec["serviceName"] == "mf-megooflags-worker-headless"
    selector = spec["selector"]["matchLabels"]
    assert selector["app.kubernetes.io/component"] == "worker"
    assert spec["template"]["metadata"]["labels"] == selector
    service = find(docs, "Service", "mf-megooflags-worker")
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"] == selector
    assert service["spec"]["ports"] == [
        {"name": "http", "port": 8000, "targetPort": "http", "protocol": "TCP"}
    ]
    headless = find(docs, "Service", "mf-megooflags-worker-headless")
    assert headless["spec"]["clusterIP"] == "None"
    assert headless["spec"]["publishNotReadyAddresses"] is True
    assert headless["spec"]["selector"] == selector


def test_worker_probes(tmp_path: Path) -> None:
    pod = _worker_set(render(tmp_path, WORKERS))["spec"]["template"]
    worker = _container(pod, "worker")
    assert worker["ports"] == [{"name": "http", "containerPort": 8000, "protocol": "TCP"}]
    assert worker["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert worker["livenessProbe"]["periodSeconds"] == 10
    assert worker["livenessProbe"]["failureThreshold"] == 3
    assert worker["readinessProbe"]["httpGet"] == {"path": "/readyz", "port": "http"}
    assert worker["readinessProbe"]["periodSeconds"] == 5
    assert worker["startupProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}


def test_worker_pod_settings(tmp_path: Path) -> None:
    toleration = {"key": "dedicated", "operator": "Exists", "effect": "NoSchedule"}
    values = _workers(
        resources={"limits": {"memory": "256Mi"}},
        extraEnv=[{"name": "EXTRA", "value": "1"}],
        podAnnotations={"example.com/w": "1"},
        nodeSelector={"pool": "edge"},
        tolerations=[toleration],
        affinity={"nodeAffinity": {}},
    )
    pod = _worker_set(render(tmp_path, values))["spec"]["template"]
    assert pod["metadata"]["annotations"]["example.com/w"] == "1"
    assert "checksum/config" in pod["metadata"]["annotations"]
    assert "checksum/secret" in pod["metadata"]["annotations"]
    assert pod["spec"]["nodeSelector"] == {"pool": "edge"}
    assert pod["spec"]["tolerations"] == [toleration]
    assert pod["spec"]["affinity"] == {"nodeAffinity": {}}
    worker = _container(pod, "worker")
    assert worker["env"] == [{"name": "EXTRA", "value": "1"}]
    assert worker["resources"] == {"limits": {"memory": "256Mi"}}


ROUTED = [
    pytest.param("standalone", "Caddyfile", "mf-megooflags-api", id="standalone"),
    pytest.param("control-plane", "Caddyfile", "mf-megooflags-api", id="control-plane"),
    pytest.param("worker", "Caddyfile.worker", "mf-megooflags-worker", id="worker"),
]
API_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}


def _routed(role: str, **routing: Any) -> dict[str, Any]:
    return {**_role_values(role), **routing}


def _route_rules(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = find(docs, "HTTPRoute", "mf-megooflags")["spec"]["rules"]
    return rules


def _api_rule(rules: list[dict[str, Any]]) -> dict[str, Any]:
    (rule,) = [r for r in rules if r["backendRefs"][0]["port"] == 8000]
    return rule


@pytest.mark.parametrize(("role", "caddyfile", "service"), ROUTED)
def test_ingress_paths_match_caddyfile(
    tmp_path: Path, role: str, caddyfile: str, service: str
) -> None:
    docs = render(tmp_path, _routed(role, ingress={"enabled": True}))
    ingress = find(docs, "Ingress", "mf-megooflags")
    assert ingress["apiVersion"] == "networking.k8s.io/v1"
    (rule,) = ingress["spec"]["rules"]
    assert "host" not in rule
    api: set[tuple[str, str]] = set()
    other: set[tuple[str, str, str, int]] = set()
    for path in rule["http"]["paths"]:
        backend = path["backend"]["service"]
        key = (path["pathType"], path["path"])
        if backend["name"] == service:
            assert backend["port"] == {"number": 8000}
            api.add(key)
        else:
            other.add((*key, backend["name"], backend["port"]["number"]))
    assert api == caddy_paths(caddyfile)
    if role == "worker":
        assert other == set()
    else:
        assert other == {("Prefix", "/", "mf-megooflags-web", 3000)}
    assert "/metrics" not in {p["path"] for p in rule["http"]["paths"]}


@pytest.mark.parametrize(("role", "caddyfile", "service"), ROUTED)
def test_httproute_paths_match_caddyfile(
    tmp_path: Path, role: str, caddyfile: str, service: str
) -> None:
    parent = {"name": "gw", "namespace": "infra"}
    values = _routed(role, httpRoute={"enabled": True, "parentRefs": [parent]})
    route = find(render(tmp_path, values), "HTTPRoute", "mf-megooflags")
    assert route["apiVersion"] == "gateway.networking.k8s.io/v1"
    assert route["spec"]["parentRefs"] == [parent]
    assert "hostnames" not in route["spec"]
    api: set[tuple[str, str]] = set()
    other: set[tuple[str, str, str, int]] = set()
    for rule in route["spec"]["rules"]:
        (backend,) = rule["backendRefs"]
        for match in rule["matches"]:
            kind = "Prefix" if match["path"]["type"] == "PathPrefix" else match["path"]["type"]
            key = (kind, match["path"]["value"])
            if backend["name"] == service:
                assert backend["port"] == 8000
                api.add(key)
            else:
                other.add((*key, backend["name"], backend["port"]))
    assert api == caddy_paths(caddyfile)
    if role == "worker":
        assert other == set()
    else:
        assert other == {("Prefix", "/", "mf-megooflags-web", 3000)}


@pytest.mark.parametrize("role", ["control-plane", "worker"])
def test_httproute_api_headers_and_timeout(tmp_path: Path, role: str) -> None:
    rules = _route_rules(render(tmp_path, _routed(role, httpRoute={"enabled": True})))
    api = _api_rule(rules)
    (modifier,) = api["filters"]
    assert modifier["type"] == "ResponseHeaderModifier"
    headers = modifier["responseHeaderModifier"]["set"]
    assert {h["name"]: h["value"] for h in headers} == API_HEADERS
    assert api["timeouts"] == {"request": "0s"}
    for rule in rules:
        if rule is not api:
            assert "filters" not in rule
            assert "timeouts" not in rule


def test_httproute_switches_off(tmp_path: Path) -> None:
    route = {"enabled": True, "apiResponseHeaders": False, "timeouts": {"request": ""}}
    rules = _route_rules(render(tmp_path, _routed("control-plane", httpRoute=route)))
    for rule in rules:
        assert "filters" not in rule
        assert "timeouts" not in rule
    route = {"enabled": True, "timeouts": {"request": "1h"}}
    rules = _route_rules(render(tmp_path, _routed("control-plane", httpRoute=route)))
    assert _api_rule(rules)["timeouts"] == {"request": "1h"}


def test_httproute_hostnames(tmp_path: Path) -> None:
    route = {"enabled": True, "hostnames": ["flags.example.com"]}
    doc = find(
        render(tmp_path, _routed("control-plane", httpRoute=route)), "HTTPRoute", "mf-megooflags"
    )
    assert doc["spec"]["hostnames"] == ["flags.example.com"]


def test_ingress_passthrough(tmp_path: Path) -> None:
    tls = [{"hosts": ["flags.example.com"], "secretName": "flags-tls"}]
    annotations = {"traefik.ingress.kubernetes.io/router.entrypoints": "web"}
    ingress_values = {
        "enabled": True,
        "className": "traefik",
        "annotations": annotations,
        "host": "flags.example.com",
        "tls": tls,
    }
    docs = render(tmp_path, _routed("control-plane", ingress=ingress_values))
    ingress = find(docs, "Ingress", "mf-megooflags")
    assert ingress["metadata"]["annotations"] == annotations
    assert ingress["spec"]["ingressClassName"] == "traefik"
    assert ingress["spec"]["tls"] == tls
    (rule,) = ingress["spec"]["rules"]
    assert rule["host"] == "flags.example.com"
    plain = find(
        render(tmp_path, _routed("control-plane", ingress={"enabled": True})),
        "Ingress",
        "mf-megooflags",
    )
    assert "ingressClassName" not in plain["spec"]
    assert "tls" not in plain["spec"]
    assert "annotations" not in plain["metadata"]


@pytest.mark.parametrize("role", ["standalone", "control-plane", "worker"])
def test_routing_off_by_default(tmp_path: Path, role: str) -> None:
    kinds = {kind for kind, _ in by_kind(render(tmp_path, _role_values(role)))}
    assert not kinds & {"Ingress", "HTTPRoute"}

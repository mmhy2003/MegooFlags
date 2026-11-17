from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _api_environment(text: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index("  api:")
    env = lines.index("    environment:", start)
    block: list[str] = []
    for line in lines[env + 1 :]:
        if not line.startswith("      "):
            break
        block.append(line.strip())
    return block


def test_control_plane_file_matches_single_node() -> None:
    single = (DEPLOY / "docker-compose.yml").read_bytes()
    control_plane = (DEPLOY / "docker-compose.control-plane.yml").read_bytes()
    expected = single.replace(
        b"${MEGOOFLAGS_ROLE:-standalone}", b"${MEGOOFLAGS_ROLE:-control-plane}"
    )
    assert control_plane == expected


def test_role_passed_to_api() -> None:
    text = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MEGOOFLAGS_ROLE: ${MEGOOFLAGS_ROLE:-standalone}" in _api_environment(text)


def test_dockerfile_creates_data_dir() -> None:
    text = (ROOT / "api" / "Dockerfile").read_text(encoding="utf-8")
    assert "mkdir -p /var/lib/megooflags" in text
    assert "chown 10001" in text


def _services(text: str) -> list[str]:
    lines = text.splitlines()
    start = lines.index("services:")
    names: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        if line.startswith("  ") and not line.startswith("   ") and line.endswith(":"):
            names.append(line.strip().rstrip(":"))
    return names


def test_worker_bundle_settings() -> None:
    text = (DEPLOY / "docker-compose.worker.yml").read_text(encoding="utf-8")
    assert text.startswith("name: megooflags-worker\n")
    env = _api_environment(text)
    assert "MEGOOFLAGS_ROLE: worker" in env
    for name in ("MEGOOFLAGS_CONTROL_PLANE_URL", "MEGOOFLAGS_NODE_TOKEN"):
        assert any(line.startswith(f"{name}: ${{{name}:?") for line in env), name
    for name in (
        "MEGOOFLAGS_CONTROL_PLANE_INSECURE",
        "MEGOOFLAGS_CONTROL_PLANE_CA_FILE",
        "MEGOOFLAGS_WORKER_BUFFER_MAX_MB",
        "MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS",
        "MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE",
        "MEGOOFLAGS_SSE_HEARTBEAT_SECONDS",
        "MEGOOFLAGS_LOG_LEVEL",
    ):
        assert any(line.startswith(f"{name}: ${{{name}:-") for line in env), name
    assert "- worker_data:/var/lib/megooflags" in text
    assert "- ./certs:/etc/megooflags/certs:ro" in text
    assert "- ./Caddyfile.worker:/etc/caddy/Caddyfile:ro" in text
    assert _services(text) == ["caddy", "api"]


def test_worker_caddyfile_routes() -> None:
    text = (DEPLOY / "Caddyfile.worker").read_text(encoding="utf-8")
    assert "{$MEGOOFLAGS_SITE_ADDRESS::80} {" in text
    assert "@api path /sdk/* /ofrep/* /healthz /readyz\n" in text
    assert "handle @api {\n\t\treverse_proxy api:8000 {\n\t\t\tflush_interval -1\n" in text
    assert text.count("reverse_proxy") == 1
    assert text.endswith("\thandle {\n\t\trespond 404\n\t}\n}\n")
    assert "Content-Security-Policy" not in text
    assert "X-Content-Type-Options nosniff" in text
    assert "trusted_proxies static {$MEGOOFLAGS_TRUSTED_PROXIES:private_ranges}" in text


def test_env_worker_example_lists_settings() -> None:
    text = (DEPLOY / ".env.worker.example").read_text(encoding="utf-8")
    for name in (
        "MEGOOFLAGS_VERSION",
        "MEGOOFLAGS_CONTROL_PLANE_URL",
        "MEGOOFLAGS_NODE_TOKEN",
        "MEGOOFLAGS_CONTROL_PLANE_INSECURE",
        "MEGOOFLAGS_CONTROL_PLANE_CA_FILE",
        "MEGOOFLAGS_DATA_DIR",
        "MEGOOFLAGS_WORKER_BUFFER_MAX_MB",
        "MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS",
        "MEGOOFLAGS_SITE_ADDRESS",
        "MEGOOFLAGS_HTTP_PORT",
        "MEGOOFLAGS_TRUSTED_PROXIES",
    ):
        assert f"{name}=" in text, name
    assert "MEGOOFLAGS_SECRET_KEY=" not in text
    assert "MEGOOFLAGS_DATABASE_URL=" not in text


def test_release_attaches_cluster_files() -> None:
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    for name in (
        "deploy/docker-compose.control-plane.yml",
        "deploy/docker-compose.worker.yml",
        "deploy/Caddyfile.worker",
        "deploy/.env.worker.example",
    ):
        assert name in text, name

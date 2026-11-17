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

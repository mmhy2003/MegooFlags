import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

MOVED = [
    "cli",
    "flags-as-code",
    "importers",
    "integrations",
    "multi-node",
    "multi-node-migration",
    "release-workflows",
    "security",
]
OLD_PATH = r"docs/(" + "|".join(MOVED) + r")\.md"
SITE_URL = "https://mmhy2003.github.io/MegooFlags/"


def test_no_references_to_old_paths() -> None:
    result = subprocess.run(
        ["git", "grep", "-n", "-E", OLD_PATH, "--", ".", ":!docs/superpowers"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize("name", MOVED)
def test_old_file_is_gone(name: str) -> None:
    assert not (ROOT / "docs" / f"{name}.md").exists()


def test_docs_readme_points_to_site() -> None:
    assert SITE_URL in (ROOT / "docs" / "README.md").read_text(encoding="utf-8")

"""Every setting and every variable of the example .env files is documented in configuration.md.

Run with `uv run --project api pytest scripts/tests/test_configuration_doc.py`: it imports the API.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api" / "src"))

from megooflags_api.settings import Settings  # noqa: E402

PAGE = ROOT / "site" / "src" / "content" / "docs" / "deploy" / "configuration.md"
EXAMPLES = (ROOT / "deploy" / ".env.example", ROOT / "deploy" / ".env.worker.example")

# `NAME=value` or a commented-out `# NAME=value`, at the start of a line.
ENV_LINE = re.compile(r"^(?:#\s*)?([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def settings_env_names() -> set[str]:
    return {"MEGOOFLAGS_" + name.upper() for name in Settings.model_fields}


def example_env_names() -> set[str]:
    names: set[str] = set()
    for path in EXAMPLES:
        names.update(ENV_LINE.findall(path.read_text(encoding="utf-8")))
    return names


def test_example_env_names_reads_set_and_commented_keys() -> None:
    names = example_env_names()
    assert "MEGOOFLAGS_VERSION" in names
    assert "MEGOOFLAGS_SCHEDULER_INTERVAL_SECONDS" in names  # "# NAME=" in .env.example
    assert "MEGOOFLAGS_DATA_DIR" in names  # "# NAME=" in .env.worker.example
    assert "POSTGRES_PASSWORD" in names
    assert "OTEL_SERVICE_NAME" in names


def test_settings_env_names_use_the_prefix() -> None:
    names = settings_env_names()
    assert "MEGOOFLAGS_DATABASE_URL" in names
    assert "MEGOOFLAGS_UPDATE_CHECK" in names


@pytest.mark.parametrize("name", sorted(settings_env_names() | example_env_names()))
def test_variable_is_documented(name: str) -> None:
    assert PAGE.is_file(), f"{PAGE.relative_to(ROOT)} is missing"
    assert f"`{name}`" in PAGE.read_text(encoding="utf-8"), f"{name} is not in configuration.md"

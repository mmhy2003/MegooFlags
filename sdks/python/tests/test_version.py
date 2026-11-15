import tomllib
from pathlib import Path

import megooflags

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version() -> None:
    assert megooflags.__version__ == "0.1.0"


def test_version_matches_pyproject() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert megooflags.__version__ == project["version"]

import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MakeRepo = Callable[..., Path]


@pytest.fixture
def make_repo(tmp_path: Path) -> MakeRepo:
    def factory(version: str = "0.1.0", api: str = "0.1.0", web: str = "0.1.0") -> Path:
        (tmp_path / "VERSION").write_text(f"{version}\n", encoding="utf-8")
        (tmp_path / "api").mkdir(exist_ok=True)
        (tmp_path / "api" / "pyproject.toml").write_text(
            f'[project]\nname="x"\nversion="{api}"\n', encoding="utf-8"
        )
        (tmp_path / "web").mkdir(exist_ok=True)
        (tmp_path / "web" / "package.json").write_text(
            json.dumps({"name": "x", "version": web}), encoding="utf-8"
        )
        return tmp_path

    return factory

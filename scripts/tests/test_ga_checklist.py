import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = ROOT / "docs" / "ga-checklist.md"

HEADINGS = [
    "## 1. P0 requirements",
    "## 2. NFR verdicts",
    "## 3. Competitor matrix",
    "## 4. Differences from the PRD",
    "## 5. Maintainer steps",
    "## 6. Open blockers",
]

# A backticked repository path: it starts with a top-level directory or file of the repository.
TOP_LEVEL = sorted(p.name for p in ROOT.iterdir() if p.name not in {".git", ".megoocolab"})
CODE = re.compile(r"`([^`\s]+)`")


def _paths(text: str) -> list[str]:
    paths = []
    for token in CODE.findall(text):
        path = token.split("::", 1)[0]
        first = path.split("/", 1)[0]
        if first in TOP_LEVEL and (path != first or "." in first):
            paths.append(path)
    return paths


def _read() -> str:
    return CHECKLIST.read_text(encoding="utf-8")


def test_checklist_has_the_six_sections_in_order() -> None:
    lines = _read().splitlines()
    found = [line for line in lines if line in HEADINGS]
    assert found == HEADINGS


def test_every_backticked_repository_path_exists() -> None:
    paths = _paths(_read())
    assert len(paths) >= 40
    missing = sorted({p for p in paths if not (ROOT / p).exists()})
    assert missing == []


def test_paths_finds_repository_paths_only() -> None:
    text = "`api/tests/test_ofrep.py::test_x` `/api/system/version` `api:8000` `VERSION` `cluster`"
    assert _paths(text) == ["api/tests/test_ofrep.py"]

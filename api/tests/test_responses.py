import json
from pathlib import Path
from typing import Any

import pytest

from megooflags_api.responses import dumps

SPEC = Path(__file__).resolve().parents[2] / "spec"
DOCUMENTS = sorted(
    [*(SPEC / "conformance" / "cases").glob("*.json"), *(SPEC / "client").glob("*.json")]
)


def has_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(v) for v in value.values())
    if isinstance(value, list):
        return any(has_float(v) for v in value)
    return False


def standard(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda p: p.name)
def test_dumps_matches_the_standard_encoder(path: Path) -> None:
    document = json.loads(path.read_text())
    items = document.values() if isinstance(document, dict) else document
    for value in [document, *items]:
        assert json.loads(dumps(value)) == value
        if not has_float(value):
            assert dumps(value) == standard(value)


def test_documents_exist() -> None:
    assert len(DOCUMENTS) >= 10


def test_dumps_falls_back_for_big_integers() -> None:
    assert dumps({"v": 2**64}) == b'{"v":18446744073709551616}'
    assert dumps({"v": -(2**70)}) == b'{"v":-1180591620717411303424}'


def test_dumps_non_ascii() -> None:
    body = dumps({"k": "grün"})
    assert "grün".encode() in body
    assert b"\\u" not in body

"""Runs the language-neutral conformance suite in spec/conformance/ against the reference engine."""

import json
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from megooflags_api.evaluation import ErrorCode, Reason, Ruleset, evaluate, parse_ruleset
from megooflags_api.evaluation.bucketing import bucket
from megooflags_api.evaluation.operators import OPERATORS

SPEC = Path(__file__).resolve().parents[2] / "spec"
CONFORMANCE = SPEC / "conformance"
CASE_FILES = sorted((CONFORMANCE / "cases").glob("*.json"))
SCHEMA_VERSION = 1


@cache
def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path.name}: unknown schema_version {data.get('schema_version')!r}")
    return data


@cache
def parsed(path: Path) -> Ruleset:
    return parse_ruleset(load(path)["ruleset"])


def case_params() -> list[Any]:
    return [
        pytest.param(path, case, id=f"{path.stem}::{case['name']}")
        for path in CASE_FILES
        for case in load(path)["tests"]
    ]


def vector_params() -> list[Any]:
    path = CONFORMANCE / "bucketing.json"
    if not path.exists():
        return []
    return [pytest.param(v, id=v["input"]) for v in load(path)["vectors"]]


def same_json(a: Any, b: Any) -> bool:
    """JSON equality: 1 == 1.0, but true != 1 and "1" != 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, int | float) or isinstance(b, int | float):
        return isinstance(a, int | float) and isinstance(b, int | float) and a == b
    if isinstance(a, str) or isinstance(b, str):
        return isinstance(a, str) and isinstance(b, str) and a == b
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_json(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same_json(a[k], b[k]) for k in a)
    return False


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        (1, 1.0, True),
        (True, 1, False),
        ("1", 1, False),
        (None, None, True),
        (None, False, False),
        ([1, {"a": True}], [1.0, {"a": True}], True),
        ([1], [1, 2], False),
        ({"a": 1}, {"a": 1, "b": 2}, False),
        ({"a": 0}, {"a": False}, False),
    ],
)
def test_same_json(a: Any, b: Any, equal: bool) -> None:
    assert same_json(a, b) is equal


@pytest.mark.parametrize(("path", "case"), case_params())
def test_case(path: Path, case: dict[str, Any]) -> None:
    result = evaluate(
        parsed(path), case["flag"], case["context"], case["default"], case.get("expected_type")
    )
    expected = case["expected"]
    assert same_json(result.value, expected["value"]), (result.value, expected["value"])
    assert result.variant == expected["variant"]
    assert result.reason == expected["reason"]
    assert result.error_code == expected["error_code"]
    for key, value in expected["metadata"].items():
        assert key in result.metadata, (key, result.metadata)
        assert same_json(result.metadata[key], value), (key, result.metadata[key], value)


@pytest.mark.parametrize("vector", vector_params())
def test_bucketing_vector(vector: dict[str, Any]) -> None:
    flag, _, text = vector["input"].partition(".")
    assert bucket(flag, text) == vector["bucket"]


def test_bucketing_has_vectors() -> None:
    assert len(vector_params()) >= 20


def schema_validator(name: str) -> Draft202012Validator:
    schema = json.loads((SPEC / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_case_files_match_schema() -> None:
    assert CASE_FILES, "no case files"
    validator = schema_validator("conformance/conformance.schema.json")
    for path in CASE_FILES:
        errors = [e.message for e in validator.iter_errors(load(path))]
        assert not errors, (path.name, errors)


def test_rulesets_match_schema() -> None:
    assert CASE_FILES, "no case files"
    validator = schema_validator("ruleset.schema.json")
    for path in CASE_FILES:
        errors = [e.message for e in validator.iter_errors(load(path)["ruleset"])]
        assert not errors, (path.name, errors)


@pytest.mark.parametrize("path", CASE_FILES, ids=[p.stem for p in CASE_FILES])
def test_names_unique_per_file(path: Path) -> None:
    names = [case["name"] for case in load(path)["tests"]]
    assert len(names) == len(set(names))


def clauses(data: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for flag in data["flags"].values():
        for rule in flag["rules"]:
            yield from rule["clauses"]
    for segment in data["segments"].values():
        for segment_rule in segment["rules"]:
            yield from segment_rule["clauses"]


def test_suite_is_complete() -> None:
    operators = {c["operator"] for p in CASE_FILES for c in clauses(load(p)["ruleset"])}
    expected = [case["expected"] for p in CASE_FILES for case in load(p)["tests"]]
    assert set(OPERATORS) - operators == set()
    assert {r.value for r in Reason} - {e["reason"] for e in expected} == set()
    codes = {c.value for c in ErrorCode} - {ErrorCode.PARSE_ERROR.value}
    assert codes - {e["error_code"] for e in expected} == set()

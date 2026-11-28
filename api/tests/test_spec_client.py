"""The shared client SDK cases in spec/client stay valid and true to the server."""

import hashlib
import json
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from megooflags_api.etags import canonical_json
from megooflags_api.schemas.sdk import SdkEvaluationResult

CLIENT = Path(__file__).resolve().parents[2] / "spec" / "client"
CASE_FILES = ("resolution.json", "context.json")

REQUIRED_RESOLUTION_CASES = (
    "boolean-served-from-network",
    "string-served-from-network",
    "integer-served-from-network",
    "number-served-from-network",
    "object-served-from-network",
    "boolean-served-from-cache",
    "string-served-from-cache",
    "integer-served-from-cache",
    "number-served-from-cache",
    "object-served-from-cache",
    "prerequisite-failed-passed-through",
    "null-variant-without-error-returns-default",
    "stored-type-mismatch",
    "stored-parse-error",
    "stored-invalid-context",
    "stored-general",
    "stored-type-mismatch-from-cache",
    "stored-parse-error-from-cache",
    "stored-invalid-context-from-cache",
    "stored-general-from-cache",
    "unknown-stored-error-code-is-general",
    "boolean-read-of-string-is-type-mismatch",
    "string-read-of-number-is-type-mismatch",
    "number-read-of-boolean-is-type-mismatch",
    "object-read-of-string-is-type-mismatch",
    "integer-read-of-fraction-is-type-mismatch",
    "integer-read-of-out-of-range-is-type-mismatch",
    "integer-read-of-3-served",
    "integer-read-of-3.0-served",
    "number-read-of-integer-served",
    "object-read-of-object-served",
    "flag-missing-is-flag-not-found",
    "no-results-is-provider-not-ready",
)

REQUIRED_CONTEXT_CASES = (
    "targeting-key-becomes-key",
    "targeting-key-replaces-key-attribute",
    "no-targeting-key-keeps-key-attribute",
    "empty-targeting-key-is-absent",
    "nested-objects-and-lists",
    "booleans-null-and-numbers",
    "date-becomes-utc-milliseconds",
    "input-key-order-does-not-matter",
    "keys-sorted-by-code-point",
    "string-escapes",
)


@cache
def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CLIENT / name).read_text(encoding="utf-8"))
    return data


def cases_of(name: str) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = load(name)["tests"]
    return tests


def to_json_value(value: Any) -> Any:
    """Dates (`{"$date": ...}`) to UTC milliseconds, integral floats to ints, recursively."""
    if isinstance(value, dict):
        if set(value) == {"$date"}:
            moment = datetime.fromisoformat(value["$date"]).astimezone(UTC)
            return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
        return {key: to_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_json_value(item) for item in value]
    if isinstance(value, float) and value.is_integer() and abs(value) < 2**53:
        return int(value)
    return value


def to_request_context(context: dict[str, Any]) -> dict[str, Any]:
    """The reference of spec/client/README.md: an OpenFeature context as an evaluate context."""
    body = {key: to_json_value(value) for key, value in context.items() if key != "targetingKey"}
    targeting_key = context.get("targetingKey")
    if isinstance(targeting_key, str) and targeting_key:
        body["key"] = targeting_key
    return body


def canonical(value: Any) -> str:
    return canonical_json(to_json_value(value))


def context_hash(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@cache
def schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CLIENT / "client.schema.json").read_text())
    return data


def test_schema_is_valid_2020_12() -> None:
    Draft202012Validator.check_schema(schema())


@pytest.mark.parametrize("name", CASE_FILES)
def test_case_files_validate(name: str) -> None:
    Draft202012Validator(schema()).validate(load(name))


def test_schema_refuses_another_version() -> None:
    data = {**load("context.json"), "schema_version": 2}
    assert not Draft202012Validator(schema()).is_valid(data)


@pytest.mark.parametrize("name", CASE_FILES)
def test_names_are_unique(name: str) -> None:
    names = [test["name"] for test in cases_of(name)]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("test", cases_of("resolution.json"), ids=lambda test: test["name"])
def test_stored_results_match_the_server_schema(test: dict[str, Any]) -> None:
    for result in test["results"] or []:
        SdkEvaluationResult.model_validate(result)
        assert "metadata" not in result


@pytest.mark.parametrize("test", cases_of("context.json"), ids=lambda test: test["name"])
def test_context_cases_match_the_reference(test: dict[str, Any]) -> None:
    body = to_request_context(test["context"])
    assert canonical(test["body"]) == canonical(body)
    assert test["canonical"] == canonical(body)
    assert test["context_hash"] == context_hash(body)


def test_required_resolution_cases_exist() -> None:
    readme = (CLIENT / "README.md").read_text(encoding="utf-8")
    names = {test["name"] for test in cases_of("resolution.json")}
    for name in REQUIRED_RESOLUTION_CASES:
        assert f"`{name}`" in readme
        assert name in names


def test_required_context_cases_exist() -> None:
    readme = (CLIENT / "README.md").read_text(encoding="utf-8")
    names = {test["name"] for test in cases_of("context.json")}
    for name in REQUIRED_CONTEXT_CASES:
        assert f"`{name}`" in readme
        assert name in names

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from test_evaluation_parse import ruleset as full_ruleset

SPEC = Path(__file__).resolve().parents[2] / "spec"


@cache
def ruleset_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((SPEC / "ruleset.schema.json").read_text())
    return schema


def validator() -> Draft202012Validator:
    return Draft202012Validator(ruleset_schema())


def minimal() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project": "p",
        "environment": "e",
        "flags": {
            "f": {
                "key": "f",
                "type": "boolean",
                "enabled": True,
                "version": 1,
                "variations": [{"key": "on", "value": True}, {"key": "off", "value": False}],
                "off_variation": "off",
                "prerequisites": [],
                "targets": [],
                "rules": [],
                "fallthrough": {"variation": "on"},
            }
        },
        "segments": {},
    }


def test_ruleset_schema_is_valid_2020_12() -> None:
    Draft202012Validator.check_schema(ruleset_schema())


@pytest.mark.parametrize("data", [minimal(), full_ruleset()], ids=["minimal", "full"])
def test_valid_ruleset_validates(data: dict[str, Any]) -> None:
    validator().validate(data)


def test_serve_with_variation_and_rollout_is_invalid() -> None:
    data = minimal()
    data["flags"]["f"]["fallthrough"] = {
        "variation": "on",
        "rollout": {"bucket_by": "key", "weights": [{"variation": "on", "weight": 100000}]},
    }
    assert not validator().is_valid(data)


def test_unknown_operator_is_invalid() -> None:
    data = minimal()
    data["flags"]["f"]["rules"] = [
        {
            "description": "",
            "clauses": [
                {"attribute": "key", "operator": "regex", "values": ["a"], "negate": False}
            ],
            "serve": {"variation": "on"},
        }
    ]
    assert not validator().is_valid(data)


def test_flag_missing_off_variation_is_invalid() -> None:
    data = minimal()
    del data["flags"]["f"]["off_variation"]
    assert not validator().is_valid(data)

from typing import Any

import httpx
import pytest
from helpers import assert_error, create_flag, create_project
from pydantic import ValidationError

from megooflags_api.errors import ApiError
from megooflags_api.models.enums import Operator
from megooflags_api.schemas.targeting import ClauseIn
from megooflags_api.services.clauses import check_clause

PATH = "rules[0].clauses[0]"

CASES: list[tuple[str, list[Any], bool]] = [
    ("in", ["a", 1, True], True),
    ("not_in", ["a", 1, True], True),
    ("contains", ["a"], True),
    ("contains", [1], False),
    ("starts_with", ["a"], True),
    ("starts_with", [1], False),
    ("ends_with", ["a"], True),
    ("ends_with", [1], False),
    ("matches", ["^a.*"], True),
    ("matches", ["("], False),
    ("matches", ["a(?=b)"], False),
    ("matches", ["(?<!a)b"], False),
    ("matches", ["(a)\\1"], False),
    ("matches", ["(?P<x>a)(?P=x)"], False),
    ("matches", ["(?>a)"], False),
    ("matches", ["a*+"], False),
    ("matches", ["a{2}+"], False),
    ("matches", ["(a)?(?(1)b|c)"], False),
    ("matches", ["[(?=]x"], True),
    ("matches", ["\\(\\?=a"], True),
    ("matches", ["(?P<x>a)b"], True),
    ("matches", ["\\d+\\.\\d+"], True),
    ("lt", [1, 2.5], True),
    ("lt", ["1"], False),
    ("lt", [True], False),
    ("lte", [1, 2.5], True),
    ("lte", ["1"], False),
    ("lte", [True], False),
    ("gt", [1, 2.5], True),
    ("gt", ["1"], False),
    ("gt", [True], False),
    ("gte", [1, 2.5], True),
    ("gte", ["1"], False),
    ("gte", [True], False),
    ("semver_eq", ["1.2.3", "1.0.0-rc.1+b5"], True),
    ("semver_eq", ["1.2"], False),
    ("semver_eq", ["v1.2.3"], False),
    ("semver_eq", ["1.2.3\n"], False),
    ("semver_lt", ["1.2.3", "1.0.0-rc.1+b5"], True),
    ("semver_lt", ["1.2"], False),
    ("semver_lt", ["v1.2.3"], False),
    ("semver_gt", ["1.2.3", "1.0.0-rc.1+b5"], True),
    ("semver_gt", ["1.2"], False),
    ("semver_gt", ["v1.2.3"], False),
    ("before", ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00.5+02:00"], True),
    ("before", ["2026-01-01"], False),
    ("before", [1700000000], False),
    ("after", ["2026-01-01T00:00:00Z", "2026-01-01T00:00:00.5+02:00"], True),
    ("after", ["2026-01-01"], False),
    ("after", [1700000000], False),
    ("in_segment", ["beta"], True),
    ("in_segment", [1], False),
]


@pytest.mark.parametrize(("operator", "values", "ok"), CASES)
def test_check_clause(operator: str, values: list[Any], ok: bool) -> None:
    clause = ClauseIn(operator=Operator(operator), values=values)
    if ok:
        check_clause(clause, PATH, allow_segments=True)
        return
    with pytest.raises(ApiError) as info:
        check_clause(clause, PATH, allow_segments=True)
    assert info.value.status == 422
    assert info.value.code == "validation_error"
    assert info.value.details == {"path": f"{PATH}.values[0]"}


def test_reports_the_first_bad_value() -> None:
    clause = ClauseIn(operator=Operator.LT, values=[1, "x", "y"])
    with pytest.raises(ApiError) as info:
        check_clause(clause, PATH, allow_segments=True)
    assert info.value.details == {"path": f"{PATH}.values[1]"}


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_numbers_must_be_finite(value: float) -> None:
    with pytest.raises(ApiError):
        check_clause(ClauseIn(operator=Operator.GT, values=[value]), PATH, allow_segments=True)


def test_in_segment_not_allowed_in_segment_rules() -> None:
    clause = ClauseIn(operator=Operator.IN_SEGMENT, values=["beta"])
    with pytest.raises(ApiError) as info:
        check_clause(clause, PATH, allow_segments=False)
    assert info.value.status == 422
    assert info.value.code == "validation_error"
    assert info.value.details == {"path": f"{PATH}.operator"}


def test_other_operators_allowed_without_segments() -> None:
    check_clause(ClauseIn(operator=Operator.IN, values=["a"]), PATH, allow_segments=False)


def test_values_must_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        ClauseIn(operator=Operator.IN, values=[])


def test_attribute_defaults_to_key() -> None:
    assert ClauseIn(operator=Operator.IN, values=["a"]).attribute == "key"


async def test_put_config_refuses_lookahead(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    body = {
        "version": 1,
        "enabled": True,
        "rules": [
            {
                "clauses": [{"attribute": "email", "operator": "matches", "values": ["a(?=b)"]}],
                "variation": "true",
            }
        ],
        "fallthrough": {"variation": "false"},
    }

    r = await api.put("/api/projects/demo/flags/new-ui/environments/production", json=body)

    error = assert_error(r, 422, "validation_error")
    assert error["details"] == {"path": "rules[0].clauses[0].values[0]"}

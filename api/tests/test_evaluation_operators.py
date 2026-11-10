import re
from typing import Any

import pytest

from megooflags_api.evaluation.operators import (
    OPERATORS,
    json_equal,
    match_value,
    parse_semver,
    prepare,
    semver_compare,
    to_millis,
    value_has_type,
)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (1, 1.0, True),
        ("1", 1, False),
        (True, 1, False),
        (1, True, False),
        (False, False, True),
        (False, 0, False),
        ("a", "a", True),
        ("a", "b", False),
        (None, None, False),
    ],
)
def test_json_equal(a: Any, b: Any, expected: bool) -> None:
    assert json_equal(a, b) is expected


def test_operators() -> None:
    assert len(OPERATORS) == 16
    assert "in_segment" in OPERATORS


SEMVER_CHAIN = [
    "1.0.0-alpha",
    "1.0.0-alpha.1",
    "1.0.0-alpha.beta",
    "1.0.0-beta",
    "1.0.0-beta.2",
    "1.0.0-beta.11",
    "1.0.0-rc.1",
    "1.0.0",
    "1.9.0",
    "1.10.0",
    "2.0.0",
]


@pytest.mark.parametrize(
    ("lower", "higher"), list(zip(SEMVER_CHAIN, SEMVER_CHAIN[1:], strict=False))
)
def test_semver_precedence(lower: str, higher: str) -> None:
    a, b = parse_semver(lower), parse_semver(higher)
    assert a is not None and b is not None
    assert semver_compare(a, b) == -1
    assert semver_compare(b, a) == 1
    assert semver_compare(a, a) == 0


def test_semver_build_metadata_is_ignored() -> None:
    a, b = parse_semver("1.0.0+b1"), parse_semver("1.0.0+b2")
    assert a is not None and b is not None
    assert semver_compare(a, b) == 0


@pytest.mark.parametrize("value", ["v1.0.0", "1.0", "01.0.0", "1.0.0\n", "١.٢.٣", 1, None, True])
def test_parse_semver_invalid(value: Any) -> None:
    assert parse_semver(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1970-01-01T00:00:01Z", 1000),
        ("1970-01-01T01:00:00+01:00", 0),
        ("1970-01-01t00:00:00.123456789z", 123),
        ("1970-01-01 00:00:00.5Z", 500),
        (1500.9, 1500),
        (1500, 1500),
        (-0.5, -1),
        ("2026-01-01", None),
        (True, None),
        ("2026-13-01T00:00:00Z", None),
        ("1970-01-01T00:00:01Z\n", None),
        (float("inf"), None),
        (float("nan"), None),
        (None, None),
    ],
)
def test_to_millis(value: Any, expected: int | None) -> None:
    assert to_millis(value) == expected


def test_prepare() -> None:
    (pattern,) = prepare("matches", ["a+"])
    assert isinstance(pattern, re.Pattern)
    assert pattern.flags & re.ASCII
    assert prepare("matches", ["(", 1]) == (None, None)
    assert prepare("semver_eq", ["1.2.3", "bad"]) == (parse_semver("1.2.3"), None)
    assert prepare("before", ["1970-01-01T00:00:01Z", 1000, "x"]) == (1000, None, None)
    assert prepare("in", ["a", 1, True]) == ("a", 1, True)


def m(operator: str, element: Any, *values: Any) -> bool:
    return match_value(operator, element, prepare(operator, values))


@pytest.mark.parametrize(
    ("operator", "element", "values", "expected"),
    [
        ("in", "a", ["b", "a"], True),
        ("in", 1, [1.0], True),
        ("in", True, [1], False),
        ("in", "c", ["a", "b"], False),
        ("not_in", "c", ["a", "b"], True),
        ("not_in", "a", ["a", "b"], False),
        ("not_in", {"a": 1}, ["b"], False),
        ("not_in", [1], [2], False),
        ("contains", "abc", ["x", "b"], True),
        ("contains", "abc", ["x"], False),
        ("contains", 123, ["2"], False),
        ("starts_with", "abc", ["ab"], True),
        ("starts_with", "abc", ["bc"], False),
        ("ends_with", "abc", ["bc"], True),
        ("ends_with", "abc", ["ab"], False),
        ("matches", "abc", ["b"], True),
        ("matches", "abc", ["^b"], False),
        ("matches", "٣", ["\\d"], False),
        ("matches", "3", ["\\d"], True),
        ("matches", "abc", ["("], False),
        ("matches", 1, ["1"], False),
        ("lt", 1, [2], True),
        ("lt", 2, [2], False),
        ("lt", "1", [2], False),
        ("lt", True, [2], False),
        ("lte", 2, [2], True),
        ("lte", 3, [2], False),
        ("gt", 3, [2.5], True),
        ("gt", 2, [2], False),
        ("gte", 2, [2.0], True),
        ("gte", 1, [2], False),
        ("semver_eq", "1.0.0+b1", ["1.0.0"], True),
        ("semver_eq", "1.0.1", ["1.0.0"], False),
        ("semver_eq", "bad", ["1.0.0"], False),
        ("semver_lt", "1.0.0-rc.1", ["1.0.0"], True),
        ("semver_lt", "1.0.0", ["1.0.0"], False),
        ("semver_gt", "1.10.0", ["1.9.0"], True),
        ("semver_gt", "1.9.0", ["1.10.0", "bad"], False),
        ("before", "1970-01-01T00:00:00Z", ["1970-01-01T00:00:01Z"], True),
        ("before", 0, ["1970-01-01T00:00:01Z"], True),
        ("before", 0, [1000], False),
        ("before", "1970-01-01", ["1970-01-02T00:00:00Z"], False),
        ("after", "1970-01-01T00:00:02Z", ["1970-01-01T00:00:01Z"], True),
        ("after", 1000.5, [1000], False),
        ("after", True, ["1970-01-01T00:00:00Z"], False),
        ("in_segment", "a", ["a"], False),
        ("unknown", "a", ["a"], False),
    ],
)
def test_match_value(operator: str, element: Any, values: list[Any], expected: bool) -> None:
    assert m(operator, element, *values) is expected


def test_match_value_prepared_none_never_matches() -> None:
    assert match_value("matches", "abc", (None,)) is False
    assert match_value("semver_eq", "1.0.0", (None,)) is False
    assert match_value("before", 0, (None,)) is False


@pytest.mark.parametrize(
    ("value", "expected_type", "expected"),
    [
        (True, "boolean", True),
        (1, "boolean", False),
        ("a", "string", True),
        (1, "string", False),
        (1, "number", True),
        (1.5, "number", True),
        (True, "number", False),
        ("1", "number", False),
        ({"a": 1}, "json", True),
        (None, "json", True),
        ("a", "json", True),
    ],
)
def test_value_has_type(value: Any, expected_type: str, expected: bool) -> None:
    assert value_has_type(value, expected_type) is expected


def test_value_has_type_unknown_type() -> None:
    with pytest.raises(ValueError):
        value_has_type(1, "integer")

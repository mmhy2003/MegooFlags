"""Clause operators with portable semantics. Stdlib only."""

import math
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from megooflags_api.evaluation.model import FLAG_TYPES

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$",
    re.ASCII,
)
RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$", re.ASCII
)
OPERATORS = frozenset(
    {
        "in",
        "not_in",
        "contains",
        "starts_with",
        "ends_with",
        "matches",
        "lt",
        "lte",
        "gt",
        "gte",
        "semver_eq",
        "semver_lt",
        "semver_gt",
        "before",
        "after",
        "in_segment",
    }
)
SemverKey = tuple[int, int, int, tuple[tuple[int, int, str], ...] | None]

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_FRACTION = re.compile(r"\.(\d{1,3})\d*", re.ASCII)


def is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def is_scalar(v: Any) -> bool:
    return isinstance(v, str | bool) or is_number(v)


def json_equal(a: Any, b: Any) -> bool:
    """JSON equality: a bool only equals a bool, numbers compare numerically, a str equals a str."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if is_number(a) and is_number(b):
        return bool(a == b)
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


def parse_semver(v: Any) -> SemverKey | None:
    """(major, minor, patch, prerelease key or None); None if `v` is not a SemVer 2.0.0 string."""
    if not isinstance(v, str):
        return None
    m = SEMVER.fullmatch(v)
    if m is None:
        return None
    prerelease = None
    if m.group(4) is not None:
        prerelease = tuple(
            (0, int(i), "") if i.isdigit() else (1, 0, i) for i in m.group(4).split(".")
        )
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), prerelease


def semver_compare(a: SemverKey, b: SemverKey) -> int:
    """SemVer 2.0.0 precedence: -1, 0 or 1. Build metadata is already dropped by parse_semver."""
    if a[:3] != b[:3]:
        return -1 if a[:3] < b[:3] else 1
    pa, pb = a[3], b[3]
    if pa == pb:
        return 0
    if pa is None:
        return 1
    if pb is None:
        return -1
    return -1 if pa < pb else 1


def to_millis(v: Any) -> int | None:
    """Integer milliseconds since the Unix epoch, or None if `v` is not a date."""
    if is_number(v):
        return math.floor(v) if math.isfinite(v) else None
    if not isinstance(v, str) or RFC3339.fullmatch(v) is None:
        return None
    text = _FRACTION.sub(r".\1", v.upper().replace(" ", "T"), count=1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (dt - EPOCH) // timedelta(milliseconds=1)


def _compile(v: Any) -> re.Pattern[str] | None:
    if not isinstance(v, str):
        return None
    try:
        return re.compile(v, re.ASCII)
    except re.error:
        return None


def prepare(operator: str, values: Sequence[Any]) -> tuple[Any, ...]:
    """Clause values in the form match_value needs. Unusable values become None."""
    if operator == "matches":
        return tuple(_compile(v) for v in values)
    if operator.startswith("semver_"):
        return tuple(parse_semver(v) for v in values)
    if operator in ("before", "after"):
        return tuple(to_millis(v) if isinstance(v, str) else None for v in values)
    return tuple(values)


def match_value(operator: str, element: Any, prepared: tuple[Any, ...]) -> bool:
    """Whether one scalar element of the attribute matches the clause's prepared values."""
    match operator:
        case "in":
            return any(json_equal(element, p) for p in prepared)
        case "not_in":
            return is_scalar(element) and not any(json_equal(element, p) for p in prepared)
        case "contains":
            return isinstance(element, str) and any(
                isinstance(p, str) and p in element for p in prepared
            )
        case "starts_with":
            return isinstance(element, str) and any(
                isinstance(p, str) and element.startswith(p) for p in prepared
            )
        case "ends_with":
            return isinstance(element, str) and any(
                isinstance(p, str) and element.endswith(p) for p in prepared
            )
        case "matches":
            return isinstance(element, str) and any(
                p is not None and p.search(element) is not None for p in prepared
            )
        case "lt":
            return is_number(element) and any(is_number(p) and element < p for p in prepared)
        case "lte":
            return is_number(element) and any(is_number(p) and element <= p for p in prepared)
        case "gt":
            return is_number(element) and any(is_number(p) and element > p for p in prepared)
        case "gte":
            return is_number(element) and any(is_number(p) and element >= p for p in prepared)
        case "semver_eq" | "semver_lt" | "semver_gt":
            key = parse_semver(element)
            if key is None:
                return False
            want = {"semver_eq": 0, "semver_lt": -1, "semver_gt": 1}[operator]
            return any(p is not None and semver_compare(key, p) == want for p in prepared)
        case "before":
            t = to_millis(element)
            return t is not None and any(p is not None and t < p for p in prepared)
        case "after":
            t = to_millis(element)
            return t is not None and any(p is not None and t > p for p in prepared)
    return False  # in_segment (handled by the engine) or an unknown operator


def value_has_type(value: Any, expected_type: str) -> bool:
    """Whether a variation value has the OpenFeature type asked for. `json` accepts anything."""
    if expected_type not in FLAG_TYPES:
        raise ValueError(f"unknown flag type: {expected_type!r}")
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "number":
        return is_number(value)
    return True

"""Clause operators with portable semantics. Stdlib only."""

import math
import re
from collections.abc import Sequence
from typing import Any

from megooflags._engine.model import FLAG_TYPES

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$",
    re.ASCII,
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

_DIGITS = frozenset("0123456789")


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


def _digits(s: str, start: int, end: int) -> int | None:
    """s[start:end] as an integer; None unless every character is 0-9 (str.isdigit is wider)."""
    part = s[start:end]
    if not part or not _DIGITS.issuperset(part):
        return None
    return int(part)


def _days_from_civil(y: int, m: int, d: int) -> int:
    """Days from 1970-01-01 to y-m-d, proleptic Gregorian (Howard Hinnant's days_from_civil)."""
    if m <= 2:
        y -= 1
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _days_in_month(y: int, m: int) -> int:
    if m == 2:
        return 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28
    return 30 if m in (4, 6, 9, 11) else 31


def parse_rfc3339_millis(s: str) -> int | None:
    """Milliseconds since the Unix epoch of an RFC 3339 date-time, or None if `s` is not one.

    Parsed by hand with integer arithmetic (spec/evaluation.md), not with datetime.fromisoformat,
    whose accepted forms differ between Python versions.
    """
    if len(s) < 20 or (s[4], s[7], s[13], s[16]) != ("-", "-", ":", ":") or s[10] not in "Tt ":
        return None
    year, month, day = _digits(s, 0, 4), _digits(s, 5, 7), _digits(s, 8, 10)
    hour, minute, second = _digits(s, 11, 13), _digits(s, 14, 16), _digits(s, 17, 19)
    if year is None or month is None or day is None:
        return None
    if hour is None or minute is None or second is None:
        return None
    i, millis = 19, 0
    if s[i] == ".":
        j = i + 1
        while j < len(s) and s[j] in _DIGITS:
            j += 1
        if j == i + 1:
            return None
        millis = int(s[i + 1 : min(j, i + 4)].ljust(3, "0"))
        i = j
    zone = s[i:]
    if zone in ("Z", "z"):
        offset = 0
    elif len(zone) == 6 and zone[0] in "+-" and zone[3] == ":":
        oh, om = _digits(zone, 1, 3), _digits(zone, 4, 6)
        if oh is None or om is None or om > 59 or oh * 60 + om >= 1440:
            return None
        offset = (oh * 60 + om) * (1 if zone[0] == "+" else -1)
    else:
        return None
    if year < 1 or not 1 <= month <= 12 or not 1 <= day <= _days_in_month(year, month):
        return None
    if hour > 23 or minute > 59 or second > 59:
        return None
    days = _days_from_civil(year, month, day)
    seconds = days * 86400 + hour * 3600 + minute * 60 + second - offset * 60
    return seconds * 1000 + millis


def to_millis(v: Any) -> int | None:
    """Integer milliseconds since the Unix epoch, or None if `v` is not a date."""
    if is_number(v):
        return math.floor(v) if math.isfinite(v) else None
    if not isinstance(v, str):
        return None
    return parse_rfc3339_millis(v)


_NON_PORTABLE_GROUPS = (
    ("(?=", "lookahead is not portable"),
    ("(?!", "lookahead is not portable"),
    ("(?<=", "lookbehind is not portable"),
    ("(?<!", "lookbehind is not portable"),
    ("(?>", "atomic groups are not portable"),
    ("(?(", "conditionals are not portable"),
    ("(?P=", "backreferences are not portable"),
)


def regex_portability_error(pattern: str) -> str | None:
    """Why `pattern` is outside the RE2/Python common subset (evaluation.md 6.3), or None.

    The same check the API applies when a rule is saved; such a pattern never compiles in RE2.
    """
    i, in_class = 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            nxt = pattern[i + 1 : i + 2]
            if not in_class and nxt and nxt in "123456789":
                return "backreferences are not portable"
            i += 2
            continue
        if in_class:
            in_class = c != "]"
            i += 1
            continue
        if c == "[":
            in_class = True
            i += 1
            if pattern[i : i + 1] == "^":
                i += 1
            if pattern[i : i + 1] == "]":  # a leading ] is literal
                i += 1
            continue
        for prefix, message in _NON_PORTABLE_GROUPS:
            if pattern.startswith(prefix, i):
                return message
        if c in "*+?}" and pattern[i + 1 : i + 2] == "+":
            return "possessive quantifiers are not portable"
        i += 1
    return None


def compile_regex(pattern: str) -> tuple[re.Pattern[str] | None, str | None]:
    """The compiled pattern, or None and why it does not compile in the portable subset."""
    reason = regex_portability_error(pattern)
    if reason is not None:
        return None, reason
    try:
        return re.compile(pattern, re.ASCII), None
    except re.error as e:
        return None, str(e)


def _compile(v: Any) -> re.Pattern[str] | None:
    if not isinstance(v, str):
        return None
    return compile_regex(v)[0]


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

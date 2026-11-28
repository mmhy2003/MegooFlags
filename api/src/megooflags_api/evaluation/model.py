"""Parsed ruleset and evaluation results. Stdlib only."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

FLAG_TYPES = frozenset({"boolean", "string", "number", "json"})
MAX_PREREQUISITE_DEPTH = 32


class Reason(StrEnum):
    STATIC = "STATIC"
    DEFAULT = "DEFAULT"
    TARGETING_MATCH = "TARGETING_MATCH"
    SPLIT = "SPLIT"
    DISABLED = "DISABLED"
    PREREQUISITE_FAILED = "PREREQUISITE_FAILED"
    ERROR = "ERROR"


class ErrorCode(StrEnum):
    FLAG_NOT_FOUND = "FLAG_NOT_FOUND"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    PARSE_ERROR = "PARSE_ERROR"
    INVALID_CONTEXT = "INVALID_CONTEXT"
    GENERAL = "GENERAL"


@dataclass(frozen=True, slots=True)
class Variation:
    key: str
    value: Any


@dataclass(frozen=True, slots=True)
class Weight:
    variation: str
    weight: int


@dataclass(frozen=True, slots=True)
class Rollout:
    bucket_by: str
    weights: tuple[Weight, ...]


@dataclass(frozen=True, slots=True)
class Serve:
    variation: str | None
    rollout: Rollout | None


@dataclass(frozen=True, slots=True)
class Clause:
    attribute: str
    operator: str
    values: tuple[Any, ...]
    negate: bool
    prepared: tuple[Any, ...]  # operators.prepare(operator, values)


@dataclass(frozen=True, slots=True)
class Rule:
    description: str
    clauses: tuple[Clause, ...]
    serve: Serve


@dataclass(frozen=True, slots=True)
class Target:
    variation: str
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class Prerequisite:
    flag: str
    variation: str


@dataclass(frozen=True, slots=True)
class Flag:
    key: str
    type: str
    enabled: bool
    version: int
    variations: dict[str, Variation]  # insertion order = ruleset order
    off_variation: str | None
    prerequisites: tuple[Prerequisite, ...]
    targets: tuple[Target, ...]
    rules: tuple[Rule, ...]
    fallthrough: Serve


@dataclass(frozen=True, slots=True)
class SegmentRule:
    clauses: tuple[Clause, ...]


@dataclass(frozen=True, slots=True)
class Segment:
    key: str
    included: frozenset[str]
    excluded: frozenset[str]
    rules: tuple[SegmentRule, ...]


@dataclass(frozen=True, slots=True)
class Ruleset:
    schema_version: int
    project: str
    environment: str
    flags: dict[str, Flag]
    segments: dict[str, Segment]


@dataclass(frozen=True, slots=True)
class Resolution:
    value: Any
    variant: str | None
    reason: str
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

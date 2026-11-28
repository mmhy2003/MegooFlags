"""MegooFlags evaluation engine, a copy of the API's reference engine. Stdlib only."""

from megooflags._engine.engine import evaluate, evaluate_all
from megooflags._engine.model import (
    FLAG_TYPES,
    MAX_PREREQUISITE_DEPTH,
    Clause,
    ErrorCode,
    Flag,
    Prerequisite,
    Reason,
    Resolution,
    Rollout,
    Rule,
    Ruleset,
    Segment,
    SegmentRule,
    Serve,
    Target,
    Variation,
    Weight,
)
from megooflags._engine.parse import RulesetError, parse_ruleset

__all__ = [
    "FLAG_TYPES",
    "MAX_PREREQUISITE_DEPTH",
    "Clause",
    "ErrorCode",
    "Flag",
    "Prerequisite",
    "Reason",
    "Resolution",
    "Rollout",
    "Rule",
    "RulesetError",
    "Ruleset",
    "Segment",
    "SegmentRule",
    "Serve",
    "Target",
    "Variation",
    "Weight",
    "evaluate",
    "evaluate_all",
    "parse_ruleset",
]

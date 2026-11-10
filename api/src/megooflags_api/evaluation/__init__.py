"""MegooFlags reference evaluation engine. Stdlib only: no DB, FastAPI or Pydantic imports."""

from megooflags_api.evaluation.engine import evaluate, evaluate_all
from megooflags_api.evaluation.model import (
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
from megooflags_api.evaluation.parse import RulesetError, parse_ruleset

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

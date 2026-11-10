"""Flag evaluation as specified in spec/evaluation.md. Stdlib only."""

from collections.abc import Iterable
from typing import Any

from megooflags_api.evaluation.bucketing import bucket, bucket_text, pick_variation
from megooflags_api.evaluation.model import (
    MAX_PREREQUISITE_DEPTH,
    Clause,
    ErrorCode,
    Flag,
    Prerequisite,
    Reason,
    Resolution,
    Ruleset,
    Segment,
    Serve,
)
from megooflags_api.evaluation.operators import match_value, value_has_type


class _DepthExceeded(Exception):
    """The prerequisite chain is deeper than MAX_PREREQUISITE_DEPTH (or has a cycle)."""


def evaluate(
    ruleset: Ruleset,
    flag_key: str,
    context: Any,
    default: Any,
    expected_type: str | None = None,
) -> Resolution:
    """Resolve `flag_key` for `context`. Errors return `default` with reason ERROR."""
    if not isinstance(context, dict):
        return _error(default, ErrorCode.INVALID_CONTEXT, "context must be a JSON object")
    flag = ruleset.flags.get(flag_key)
    if flag is None:
        return _error(default, ErrorCode.FLAG_NOT_FOUND, f"flag '{flag_key}' not found")
    try:
        result = _evaluate_flag(ruleset, flag, context, default, 0)
    except _DepthExceeded:
        return _error(default, ErrorCode.GENERAL, "prerequisite chain is too deep or has a cycle")
    if (
        expected_type is not None
        and result.variant is not None
        and not value_has_type(result.value, expected_type)
    ):
        return _error(
            default, ErrorCode.TYPE_MISMATCH, f"flag '{flag_key}' is not a {expected_type}"
        )
    return result


def evaluate_all(
    ruleset: Ruleset, context: Any, flag_keys: Iterable[str] | None = None
) -> dict[str, Resolution]:
    """Resolve several flags (all of them when `flag_keys` is None), sorted by key, default None."""
    keys = sorted(set(ruleset.flags if flag_keys is None else flag_keys))
    return {key: evaluate(ruleset, key, context, None) for key in keys}


def _evaluate_flag(
    ruleset: Ruleset, flag: Flag, context: dict[str, Any], default: Any, depth: int
) -> Resolution:
    if not flag.enabled:
        return _serve(flag, flag.off_variation, Reason.DISABLED, default)
    for prereq in flag.prerequisites:
        if not _prerequisite_met(ruleset, prereq, context, depth + 1):
            return _serve(
                flag,
                flag.off_variation,
                Reason.PREREQUISITE_FAILED,
                default,
                {"prerequisite": prereq.flag},
            )
    key = context.get("key")
    if isinstance(key, str):
        for target in flag.targets:
            if key in target.values:
                return _serve(flag, target.variation, Reason.TARGETING_MATCH, default)
    for index, rule in enumerate(flag.rules):
        if _all_clauses_match(ruleset, rule.clauses, context):
            return _serve_rule(
                flag, rule.serve, context, Reason.TARGETING_MATCH, default, {"rule_index": index}
            )
    fallthrough = flag.fallthrough
    if fallthrough.rollout is not None:
        reason = Reason.SPLIT
    elif not (flag.targets or flag.rules or flag.prerequisites):
        reason = Reason.STATIC
    else:
        reason = Reason.DEFAULT
    return _serve_rule(flag, fallthrough, context, reason, default)


def _prerequisite_met(
    ruleset: Ruleset, prereq: Prerequisite, context: dict[str, Any], depth: int
) -> bool:
    if depth > MAX_PREREQUISITE_DEPTH:
        raise _DepthExceeded
    pflag = ruleset.flags.get(prereq.flag)
    if pflag is None or not pflag.enabled:
        return False
    result = _evaluate_flag(ruleset, pflag, context, None, depth)
    return result.reason != Reason.ERROR and result.variant == prereq.variation


def _all_clauses_match(
    ruleset: Ruleset, clauses: tuple[Clause, ...], context: dict[str, Any]
) -> bool:
    # A plain loop instead of all(<generator>): this runs for every rule of every evaluation.
    for clause in clauses:
        if not _clause_matches(ruleset, clause, context):
            return False
    return True


def _clause_matches(ruleset: Ruleset, clause: Clause, context: dict[str, Any]) -> bool:
    if clause.operator == "in_segment":
        hit = any(_in_segment(ruleset, ruleset.segments.get(k), context) for k in clause.values)
        return hit != clause.negate
    value = context.get(clause.attribute)
    if value is None:  # missing or null: false, negate not applied
        return False
    if isinstance(value, list):
        hit = any(match_value(clause.operator, e, clause.prepared) for e in value)
    else:  # the common scalar case skips the generator: this is the engine's hot path
        hit = match_value(clause.operator, value, clause.prepared)
    return hit != clause.negate


def _in_segment(ruleset: Ruleset, segment: Segment | None, context: dict[str, Any]) -> bool:
    if segment is None:
        return False
    key = context.get("key")
    if isinstance(key, str):
        if key in segment.excluded:
            return False
        if key in segment.included:
            return True
    return any(_all_clauses_match(ruleset, rule.clauses, context) for rule in segment.rules)


def _serve(
    flag: Flag,
    variation: str | None,
    reason: Reason,
    default: Any,
    metadata: dict[str, Any] | None = None,
) -> Resolution:
    if variation is None:
        return Resolution(default, None, reason, metadata=dict(metadata or {}))
    return Resolution(
        flag.variations[variation].value, variation, reason, metadata=dict(metadata or {})
    )


def _serve_rule(
    flag: Flag,
    serve: Serve,
    context: dict[str, Any],
    reason: Reason,
    default: Any,
    metadata: dict[str, Any] | None = None,
) -> Resolution:
    if serve.rollout is None:
        return _serve(flag, serve.variation, reason, default, metadata)
    text = bucket_text(context.get(serve.rollout.bucket_by))
    value = 0 if text is None else bucket(flag.key, text)
    return _serve(flag, pick_variation(serve.rollout.weights, value), reason, default, metadata)


def _error(default: Any, code: ErrorCode, message: str) -> Resolution:
    return Resolution(default, None, Reason.ERROR, code, message, {})

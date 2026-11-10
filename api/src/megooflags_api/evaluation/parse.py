"""Ruleset JSON (spec/ruleset.schema.json) to the model dataclasses. Stdlib only.

Unknown fields are ignored. Prerequisite flag keys and segment keys are not checked: a missing
flag fails the prerequisite and a missing segment is not matched. Cycles are not checked.
"""

from typing import Any

from megooflags_api.evaluation import operators
from megooflags_api.evaluation.bucketing import BUCKETS
from megooflags_api.evaluation.model import (
    FLAG_TYPES,
    Clause,
    ErrorCode,
    Flag,
    Prerequisite,
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

SCHEMA_VERSION = 1
_MISSING = object()


class RulesetError(ValueError):
    error_code = ErrorCode.PARSE_ERROR


def _fail(path: str, message: str) -> RulesetError:
    return RulesetError(f"{path}: {message}")


def _get(obj: dict[str, Any], name: str, path: str) -> Any:
    value = obj.get(name, _MISSING)
    if value is _MISSING:
        raise _fail(f"{path}.{name}" if path else name, "is required")
    return value


def _dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(path, "must be an object")
    return value


def _list(value: Any, path: str, *, non_empty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise _fail(path, "must be an array")
    if non_empty and not value:
        raise _fail(path, "must not be empty")
    return value


def _str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise _fail(path, "must be a string")
    return value


def _bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(path, "must be a boolean")
    return value


def _int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _fail(path, "must be an integer")
    return value


def _strings(value: Any, path: str) -> frozenset[str]:
    items = _list(value, path)
    return frozenset(_str(v, f"{path}[{i}]") for i, v in enumerate(items))


def _known(value: Any, variations: dict[str, Variation], path: str) -> str:
    key = _str(value, path)
    if key not in variations:
        raise _fail(path, f"unknown variation '{key}'")
    return key


def parse_ruleset(data: Any) -> Ruleset:
    """Validate `data` and build a Ruleset. Raises RulesetError naming the bad JSON path."""
    root = _dict(data, "ruleset")
    version = _get(root, "schema_version", "")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise _fail("schema_version", f"must be {SCHEMA_VERSION}")
    project = _str(_get(root, "project", ""), "project")
    environment = _str(_get(root, "environment", ""), "environment")
    flags = _dict(_get(root, "flags", ""), "flags")
    segments = _dict(_get(root, "segments", ""), "segments")
    return Ruleset(
        schema_version=SCHEMA_VERSION,
        project=project,
        environment=environment,
        flags={key: _flag(key, value, f"flags.{key}") for key, value in flags.items()},
        segments={key: _segment(key, value, f"segments.{key}") for key, value in segments.items()},
    )


def _flag(key: str, value: Any, path: str) -> Flag:
    obj = _dict(value, path)
    if _get(obj, "key", path) != key:
        raise _fail(f"{path}.key", f"must equal '{key}'")
    flag_type = _get(obj, "type", path)
    if flag_type not in FLAG_TYPES:
        raise _fail(f"{path}.type", f"must be one of {sorted(FLAG_TYPES)}")
    variations = _variations(_get(obj, "variations", path), f"{path}.variations")
    off = _get(obj, "off_variation", path)
    off_variation = None if off is None else _known(off, variations, f"{path}.off_variation")
    prerequisites = _list(_get(obj, "prerequisites", path), f"{path}.prerequisites")
    targets = _list(_get(obj, "targets", path), f"{path}.targets")
    rules = _list(_get(obj, "rules", path), f"{path}.rules")
    return Flag(
        key=key,
        type=flag_type,
        enabled=_bool(_get(obj, "enabled", path), f"{path}.enabled"),
        version=_int(_get(obj, "version", path), f"{path}.version"),
        variations=variations,
        off_variation=off_variation,
        prerequisites=tuple(
            _prerequisite(p, f"{path}.prerequisites[{i}]") for i, p in enumerate(prerequisites)
        ),
        targets=tuple(
            _target(t, variations, f"{path}.targets[{i}]") for i, t in enumerate(targets)
        ),
        rules=tuple(_rule(r, variations, f"{path}.rules[{i}]") for i, r in enumerate(rules)),
        fallthrough=_serve(_get(obj, "fallthrough", path), variations, f"{path}.fallthrough"),
    )


def _variations(value: Any, path: str) -> dict[str, Variation]:
    variations: dict[str, Variation] = {}
    for i, item in enumerate(_list(value, path, non_empty=True)):
        item_path = f"{path}[{i}]"
        obj = _dict(item, item_path)
        key = _str(_get(obj, "key", item_path), f"{item_path}.key")
        if key in variations:
            raise _fail(f"{item_path}.key", f"duplicate variation '{key}'")
        variations[key] = Variation(key=key, value=_get(obj, "value", item_path))
    return variations


def _prerequisite(value: Any, path: str) -> Prerequisite:
    obj = _dict(value, path)
    return Prerequisite(
        flag=_str(_get(obj, "flag", path), f"{path}.flag"),
        variation=_str(_get(obj, "variation", path), f"{path}.variation"),
    )


def _target(value: Any, variations: dict[str, Variation], path: str) -> Target:
    obj = _dict(value, path)
    return Target(
        variation=_known(_get(obj, "variation", path), variations, f"{path}.variation"),
        values=_strings(_get(obj, "values", path), f"{path}.values"),
    )


def _rule(value: Any, variations: dict[str, Variation], path: str) -> Rule:
    obj = _dict(value, path)
    return Rule(
        description=_str(obj.get("description", ""), f"{path}.description"),
        clauses=_clauses(_get(obj, "clauses", path), f"{path}.clauses", segment=False),
        serve=_serve(_get(obj, "serve", path), variations, f"{path}.serve"),
    )


def _serve(value: Any, variations: dict[str, Variation], path: str) -> Serve:
    obj = _dict(value, path)
    has_variation, has_rollout = "variation" in obj, "rollout" in obj
    if has_variation == has_rollout:
        raise _fail(path, "must have exactly one of variation or rollout")
    if has_variation:
        return Serve(
            variation=_known(obj["variation"], variations, f"{path}.variation"), rollout=None
        )
    return Serve(variation=None, rollout=_rollout(obj["rollout"], variations, f"{path}.rollout"))


def _rollout(value: Any, variations: dict[str, Variation], path: str) -> Rollout:
    obj = _dict(value, path)
    bucket_by = _str(_get(obj, "bucket_by", path), f"{path}.bucket_by")
    weights_path = f"{path}.weights"
    weights: list[Weight] = []
    for i, item in enumerate(_list(_get(obj, "weights", path), weights_path, non_empty=True)):
        item_path = f"{weights_path}[{i}]"
        w = _dict(item, item_path)
        variation = _known(_get(w, "variation", item_path), variations, f"{item_path}.variation")
        weight = _int(_get(w, "weight", item_path), f"{item_path}.weight")
        if not 0 <= weight <= BUCKETS:
            raise _fail(f"{item_path}.weight", f"must be between 0 and {BUCKETS}")
        weights.append(Weight(variation=variation, weight=weight))
    if sum(w.weight for w in weights) != BUCKETS:
        raise _fail(weights_path, f"must add up to {BUCKETS}")
    return Rollout(bucket_by=bucket_by, weights=tuple(weights))


def _clauses(value: Any, path: str, *, segment: bool) -> tuple[Clause, ...]:
    items = _list(value, path, non_empty=True)
    return tuple(_clause(c, f"{path}[{i}]", segment=segment) for i, c in enumerate(items))


def _clause(value: Any, path: str, *, segment: bool) -> Clause:
    obj = _dict(value, path)
    attribute = _str(_get(obj, "attribute", path), f"{path}.attribute")
    operator = _get(obj, "operator", path)
    if operator not in operators.OPERATORS:
        raise _fail(f"{path}.operator", f"unknown operator {operator!r}")
    if segment and operator == "in_segment":
        raise _fail(f"{path}.operator", "in_segment is not allowed in a segment rule")
    values_path = f"{path}.values"
    values = tuple(_list(_get(obj, "values", path), values_path, non_empty=True))
    for i, v in enumerate(values):
        if not operators.is_scalar(v):
            raise _fail(f"{values_path}[{i}]", "must be a string, number or boolean")
    return Clause(
        attribute=attribute,
        operator=operator,
        values=values,
        negate=_bool(obj.get("negate", False), f"{path}.negate"),
        prepared=operators.prepare(operator, values),
    )


def _segment(key: str, value: Any, path: str) -> Segment:
    obj = _dict(value, path)
    rules_path = f"{path}.rules"
    rules = _list(_get(obj, "rules", path), rules_path)
    return Segment(
        key=key,
        included=_strings(_get(obj, "included", path), f"{path}.included"),
        excluded=_strings(_get(obj, "excluded", path), f"{path}.excluded"),
        rules=tuple(
            SegmentRule(
                clauses=_clauses(
                    _get(_dict(r, f"{rules_path}[{i}]"), "clauses", f"{rules_path}[{i}]"),
                    f"{rules_path}[{i}].clauses",
                    segment=True,
                )
            )
            for i, r in enumerate(rules)
        ),
    )

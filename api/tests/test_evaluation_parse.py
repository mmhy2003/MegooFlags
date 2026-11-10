import copy
from collections.abc import Callable
from typing import Any

import pytest

from megooflags_api.evaluation import ErrorCode, RulesetError, parse_ruleset
from megooflags_api.evaluation.model import Rollout, Serve, Weight


def ruleset() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project": "shop",
        "environment": "production",
        "flags": {
            "b": {
                "key": "b",
                "type": "string",
                "enabled": True,
                "version": 3,
                "variations": [
                    {"key": "z", "value": "zed"},
                    {"key": "a", "value": "ay"},
                    {"key": "m", "value": "em"},
                ],
                "off_variation": "z",
                "prerequisites": [{"flag": "a", "variation": "on"}],
                "targets": [{"variation": "a", "values": ["u1", "u2"]}],
                "rules": [
                    {
                        "description": "beta",
                        "clauses": [
                            {
                                "attribute": "email",
                                "operator": "ends_with",
                                "values": ["@x.io"],
                                "negate": False,
                            }
                        ],
                        "serve": {"variation": "m"},
                    }
                ],
                "fallthrough": {
                    "rollout": {
                        "bucket_by": "key",
                        "weights": [
                            {"variation": "z", "weight": 40000},
                            {"variation": "a", "weight": 60000},
                        ],
                    }
                },
            },
            "a": {
                "key": "a",
                "type": "boolean",
                "enabled": False,
                "version": 1,
                "variations": [{"key": "on", "value": True}, {"key": "off", "value": False}],
                "off_variation": None,
                "prerequisites": [],
                "targets": [],
                "rules": [],
                "fallthrough": {"variation": "on"},
            },
        },
        "segments": {
            "beta": {
                "included": ["u1"],
                "excluded": ["u2"],
                "rules": [
                    {
                        "clauses": [
                            {
                                "attribute": "plan",
                                "operator": "in",
                                "values": ["pro"],
                                "negate": True,
                            }
                        ]
                    }
                ],
            }
        },
    }


def test_minimal_ruleset_parses() -> None:
    rs = parse_ruleset(
        {
            "schema_version": 1,
            "project": "p",
            "environment": "e",
            "flags": {},
            "segments": {},
        }
    )
    assert (rs.schema_version, rs.project, rs.environment) == (1, "p", "e")
    assert rs.flags == {}
    assert rs.segments == {}


def test_full_ruleset_parses() -> None:
    rs = parse_ruleset(ruleset())
    assert list(rs.flags) == ["b", "a"]
    flag = rs.flags["b"]
    assert (flag.key, flag.type, flag.enabled, flag.version) == ("b", "string", True, 3)
    assert list(flag.variations) == ["z", "a", "m"]
    assert flag.variations["a"].value == "ay"
    assert flag.off_variation == "z"
    assert flag.prerequisites[0].flag == "a"
    assert flag.prerequisites[0].variation == "on"
    assert flag.targets[0].variation == "a"
    assert flag.targets[0].values == frozenset({"u1", "u2"})
    rule = flag.rules[0]
    assert rule.description == "beta"
    assert rule.serve == Serve(variation="m", rollout=None)
    clause = rule.clauses[0]
    assert (clause.attribute, clause.operator, clause.values, clause.negate) == (
        "email",
        "ends_with",
        ("@x.io",),
        False,
    )
    assert clause.prepared == ("@x.io",)
    assert flag.fallthrough == Serve(
        variation=None,
        rollout=Rollout("key", (Weight("z", 40000), Weight("a", 60000))),
    )
    assert rs.flags["a"].off_variation is None
    segment = rs.segments["beta"]
    assert segment.key == "beta"
    assert segment.included == frozenset({"u1"})
    assert segment.excluded == frozenset({"u2"})
    assert segment.rules[0].clauses[0].negate is True


def test_defaults() -> None:
    data = ruleset()
    rule = data["flags"]["b"]["rules"][0]
    del rule["description"]
    del rule["clauses"][0]["negate"]
    flag = parse_ruleset(data).flags["b"]
    assert flag.rules[0].description == ""
    assert flag.rules[0].clauses[0].negate is False


def test_unknown_fields_are_ignored() -> None:
    data = ruleset()
    data["extra"] = 1
    flag = data["flags"]["b"]
    flag["extra"] = {"x": 1}
    flag["variations"][0]["extra"] = 1
    flag["rules"][0]["extra"] = 1
    flag["rules"][0]["clauses"][0]["extra"] = 1
    flag["fallthrough"]["rollout"]["extra"] = 1
    data["segments"]["beta"]["extra"] = 1
    assert parse_ruleset(data) == parse_ruleset(ruleset())


def test_unknown_prerequisite_and_segment_keys_are_accepted() -> None:
    data = ruleset()
    data["flags"]["b"]["prerequisites"] = [{"flag": "missing", "variation": "x"}]
    data["flags"]["b"]["rules"][0]["clauses"][0] = {
        "attribute": "key",
        "operator": "in_segment",
        "values": ["nope"],
    }
    rs = parse_ruleset(data)
    assert rs.flags["b"].prerequisites[0].flag == "missing"
    assert rs.flags["b"].rules[0].clauses[0].values == ("nope",)


def test_bad_regex_parses() -> None:
    data = ruleset()
    data["flags"]["b"]["rules"][0]["clauses"][0]["operator"] = "matches"
    data["flags"]["b"]["rules"][0]["clauses"][0]["values"] = ["("]
    clause = parse_ruleset(data).flags["b"].rules[0].clauses[0]
    assert clause.values == ("(",)
    assert clause.prepared == (None,)


def test_ruleset_error_is_a_parse_error() -> None:
    assert issubclass(RulesetError, ValueError)
    assert RulesetError.error_code == ErrorCode.PARSE_ERROR


Mutation = Callable[[dict[str, Any]], Any]
B = ("flags", "b")


def _set(path: tuple[Any, ...], value: Any) -> Mutation:
    def apply(data: dict[str, Any]) -> None:
        target: Any = data
        for p in path[:-1]:
            target = target[p]
        target[path[-1]] = value

    return apply


def _del(path: tuple[Any, ...]) -> Mutation:
    def apply(data: dict[str, Any]) -> None:
        target: Any = data
        for p in path[:-1]:
            target = target[p]
        del target[path[-1]]

    return apply


ERRORS: list[tuple[str, Mutation | Any, str]] = [
    # top level
    ("not_a_dict", [], "ruleset"),
    ("schema_version_missing", _del(("schema_version",)), "schema_version"),
    ("schema_version_2", _set(("schema_version",), 2), "schema_version"),
    ("schema_version_true", _set(("schema_version",), True), "schema_version"),
    ("project", _set(("project",), 1), "project"),
    ("environment", _del(("environment",)), "environment"),
    ("flags", _set(("flags",), []), "flags"),
    ("segments", _set(("segments",), None), "segments"),
    # flag
    ("flag_not_dict", _set(B, "x"), "flags.b"),
    ("flag_key", _set((*B, "key"), "c"), "flags.b.key"),
    ("flag_type", _set((*B, "type"), "int"), "flags.b.type"),
    ("flag_enabled", _set((*B, "enabled"), 1), "flags.b.enabled"),
    ("flag_version_bool", _set((*B, "version"), True), "flags.b.version"),
    ("flag_version_str", _set((*B, "version"), "1"), "flags.b.version"),
    # variations
    ("variations_empty", _set((*B, "variations"), []), "flags.b.variations"),
    ("variations_not_list", _set((*B, "variations"), {}), "flags.b.variations"),
    ("variation_not_dict", _set((*B, "variations", 1), "a"), "flags.b.variations[1]"),
    ("variation_key", _set((*B, "variations", 1, "key"), 1), "flags.b.variations[1].key"),
    (
        "variation_value_missing",
        _del((*B, "variations", 1, "value")),
        "flags.b.variations[1].value",
    ),
    (
        "variation_duplicate",
        _set((*B, "variations", 2, "key"), "z"),
        "flags.b.variations[2].key",
    ),
    ("off_variation_unknown", _set((*B, "off_variation"), "q"), "flags.b.off_variation"),
    ("off_variation_int", _set((*B, "off_variation"), 1), "flags.b.off_variation"),
    # prerequisites
    ("prerequisites", _set((*B, "prerequisites"), {}), "flags.b.prerequisites"),
    (
        "prerequisite_not_dict",
        _set((*B, "prerequisites", 0), "a"),
        "flags.b.prerequisites[0]",
    ),
    (
        "prerequisite_flag",
        _set((*B, "prerequisites", 0, "flag"), None),
        "flags.b.prerequisites[0].flag",
    ),
    (
        "prerequisite_variation",
        _del((*B, "prerequisites", 0, "variation")),
        "flags.b.prerequisites[0].variation",
    ),
    # targets
    ("targets", _set((*B, "targets"), None), "flags.b.targets"),
    ("target_not_dict", _set((*B, "targets", 0), []), "flags.b.targets[0]"),
    (
        "target_variation",
        _set((*B, "targets", 0, "variation"), "q"),
        "flags.b.targets[0].variation",
    ),
    ("target_values", _set((*B, "targets", 0, "values"), "u1"), "flags.b.targets[0].values"),
    (
        "target_value_int",
        _set((*B, "targets", 0, "values"), ["u1", 2]),
        "flags.b.targets[0].values[1]",
    ),
    # rules
    ("rules", _set((*B, "rules"), {}), "flags.b.rules"),
    ("rule_not_dict", _set((*B, "rules", 0), 1), "flags.b.rules[0]"),
    (
        "rule_description",
        _set((*B, "rules", 0, "description"), 1),
        "flags.b.rules[0].description",
    ),
    ("rule_clauses_missing", _del((*B, "rules", 0, "clauses")), "flags.b.rules[0].clauses"),
    ("rule_clauses_empty", _set((*B, "rules", 0, "clauses"), []), "flags.b.rules[0].clauses"),
    ("rule_serve_missing", _del((*B, "rules", 0, "serve")), "flags.b.rules[0].serve"),
    # serve
    ("serve_not_dict", _set((*B, "rules", 0, "serve"), "m"), "flags.b.rules[0].serve"),
    ("serve_neither", _set((*B, "rules", 0, "serve"), {}), "flags.b.rules[0].serve"),
    (
        "serve_both",
        _set(
            (*B, "rules", 0, "serve"),
            {"variation": "m", "rollout": {"bucket_by": "key", "weights": []}},
        ),
        "flags.b.rules[0].serve",
    ),
    (
        "serve_variation_unknown",
        _set((*B, "rules", 0, "serve", "variation"), "q"),
        "flags.b.rules[0].serve.variation",
    ),
    ("fallthrough_missing", _del((*B, "fallthrough")), "flags.b.fallthrough"),
    (
        "rollout_not_dict",
        _set((*B, "fallthrough", "rollout"), []),
        "flags.b.fallthrough.rollout",
    ),
    (
        "rollout_bucket_by",
        _set((*B, "fallthrough", "rollout", "bucket_by"), 1),
        "flags.b.fallthrough.rollout.bucket_by",
    ),
    (
        "rollout_weights_empty",
        _set((*B, "fallthrough", "rollout", "weights"), []),
        "flags.b.fallthrough.rollout.weights",
    ),
    (
        "rollout_weight_not_dict",
        _set((*B, "fallthrough", "rollout", "weights", 1), 60000),
        "flags.b.fallthrough.rollout.weights[1]",
    ),
    (
        "rollout_weight_variation",
        _set((*B, "fallthrough", "rollout", "weights", 1, "variation"), "q"),
        "flags.b.fallthrough.rollout.weights[1].variation",
    ),
    (
        "rollout_weight_negative",
        _set((*B, "fallthrough", "rollout", "weights", 1, "weight"), -1),
        "flags.b.fallthrough.rollout.weights[1].weight",
    ),
    (
        "rollout_weight_too_big",
        _set((*B, "fallthrough", "rollout", "weights", 1, "weight"), 100001),
        "flags.b.fallthrough.rollout.weights[1].weight",
    ),
    (
        "rollout_weight_float",
        _set((*B, "fallthrough", "rollout", "weights", 1, "weight"), 60000.0),
        "flags.b.fallthrough.rollout.weights[1].weight",
    ),
    (
        "rollout_weight_bool",
        _set((*B, "fallthrough", "rollout", "weights", 1, "weight"), True),
        "flags.b.fallthrough.rollout.weights[1].weight",
    ),
    (
        "rollout_weights_sum",
        _set((*B, "fallthrough", "rollout", "weights", 1, "weight"), 59999),
        "flags.b.fallthrough.rollout.weights",
    ),
    # clauses
    (
        "clause_not_dict",
        _set((*B, "rules", 0, "clauses", 0), "x"),
        "flags.b.rules[0].clauses[0]",
    ),
    (
        "clause_attribute",
        _set((*B, "rules", 0, "clauses", 0, "attribute"), 1),
        "flags.b.rules[0].clauses[0].attribute",
    ),
    (
        "clause_operator",
        _set((*B, "rules", 0, "clauses", 0, "operator"), "eq"),
        "flags.b.rules[0].clauses[0].operator",
    ),
    (
        "clause_values_missing",
        _del((*B, "rules", 0, "clauses", 0, "values")),
        "flags.b.rules[0].clauses[0].values",
    ),
    (
        "clause_values_empty",
        _set((*B, "rules", 0, "clauses", 0, "values"), []),
        "flags.b.rules[0].clauses[0].values",
    ),
    (
        "clause_value_not_scalar",
        _set((*B, "rules", 0, "clauses", 0, "values"), ["a", None]),
        "flags.b.rules[0].clauses[0].values[1]",
    ),
    (
        "clause_value_list",
        _set((*B, "rules", 0, "clauses", 0, "values"), [["a"]]),
        "flags.b.rules[0].clauses[0].values[0]",
    ),
    (
        "clause_negate",
        _set((*B, "rules", 0, "clauses", 0, "negate"), "no"),
        "flags.b.rules[0].clauses[0].negate",
    ),
    # segments
    ("segment_not_dict", _set(("segments", "beta"), []), "segments.beta"),
    ("segment_included", _set(("segments", "beta", "included"), "u1"), "segments.beta.included"),
    (
        "segment_excluded",
        _set(("segments", "beta", "excluded"), [1]),
        "segments.beta.excluded[0]",
    ),
    ("segment_rules", _del(("segments", "beta", "rules")), "segments.beta.rules"),
    (
        "segment_rule_clauses",
        _set(("segments", "beta", "rules", 0, "clauses"), []),
        "segments.beta.rules[0].clauses",
    ),
    (
        "segment_clause_operator",
        _set(("segments", "beta", "rules", 0, "clauses", 0, "operator"), "eq"),
        "segments.beta.rules[0].clauses[0].operator",
    ),
    (
        "segment_in_segment",
        _set(("segments", "beta", "rules", 0, "clauses", 0, "operator"), "in_segment"),
        "segments.beta.rules[0].clauses[0].operator",
    ),
]


@pytest.mark.parametrize(
    ("mutation", "path"),
    [pytest.param(m, p, id=name) for name, m, p in ERRORS],
)
def test_errors(mutation: Mutation | Any, path: str) -> None:
    if callable(mutation):
        data: Any = copy.deepcopy(ruleset())
        mutation(data)
    else:
        data = mutation
    with pytest.raises(RulesetError) as exc:
        parse_ruleset(data)
    assert path in str(exc.value)
    assert exc.value.error_code == ErrorCode.PARSE_ERROR

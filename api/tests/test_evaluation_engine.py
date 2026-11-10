import time
from typing import Any

import pytest

from megooflags_api.evaluation import (
    ErrorCode,
    Reason,
    Resolution,
    Ruleset,
    evaluate,
    evaluate_all,
    parse_ruleset,
)
from megooflags_api.evaluation.bucketing import bucket


def flag(key: str, **overrides: Any) -> dict[str, Any]:
    """A string flag `key` with variations red/blue/green, enabled, fallthrough red."""
    data: dict[str, Any] = {
        "key": key,
        "type": "string",
        "enabled": True,
        "version": 1,
        "variations": [
            {"key": "red", "value": "#f00"},
            {"key": "blue", "value": "#00f"},
            {"key": "green", "value": "#0f0"},
        ],
        "off_variation": "red",
        "prerequisites": [],
        "targets": [],
        "rules": [],
        "fallthrough": {"variation": "red"},
    }
    data.update(overrides)
    return data


def gate(key: str, *, enabled: bool = True, serve: str = "on", **overrides: Any) -> dict[str, Any]:
    """A boolean flag with variations on/off that serves `serve`."""
    fields: dict[str, Any] = {
        "type": "boolean",
        "enabled": enabled,
        "variations": [{"key": "on", "value": True}, {"key": "off", "value": False}],
        "off_variation": "off",
        "fallthrough": {"variation": serve},
    }
    return flag(key, **(fields | overrides))


def clause(attribute: str, operator: str, *values: Any, negate: bool = False) -> dict[str, Any]:
    return {"attribute": attribute, "operator": operator, "values": list(values), "negate": negate}


def rule(*clauses: dict[str, Any], serve: dict[str, Any]) -> dict[str, Any]:
    return {"description": "", "clauses": list(clauses), "serve": serve}


def rollout(bucket_by: str, *weights: tuple[str, int]) -> dict[str, Any]:
    return {
        "rollout": {
            "bucket_by": bucket_by,
            "weights": [{"variation": v, "weight": w} for v, w in weights],
        }
    }


def ruleset(*flags: dict[str, Any], segments: dict[str, Any] | None = None) -> Ruleset:
    return parse_ruleset(
        {
            "schema_version": 1,
            "project": "shop",
            "environment": "production",
            "flags": {f["key"]: f for f in flags},
            "segments": segments or {},
        }
    )


def segment(
    key: str,
    *,
    included: list[str] | None = None,
    excluded: list[str] | None = None,
    rules: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "included": included or [],
        "excluded": excluded or [],
        "rules": [{"clauses": c} for c in rules or []],
    }


def expect(
    result: Resolution,
    value: Any,
    variant: str | None,
    reason: str,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    assert (result.value, result.variant, result.reason, result.error_code, result.metadata) == (
        value,
        variant,
        reason,
        error_code,
        metadata or {},
    )


# --- errors ---------------------------------------------------------------------------------


@pytest.mark.parametrize("context", [None, [1], "x", 3])
def test_non_object_context_is_invalid_context(context: Any) -> None:
    result = evaluate(ruleset(flag("c")), "c", context, "dflt")
    expect(result, "dflt", None, Reason.ERROR, ErrorCode.INVALID_CONTEXT)
    assert result.error_message


def test_missing_flag_is_flag_not_found() -> None:
    result = evaluate(ruleset(flag("c")), "nope", {}, "dflt")
    expect(result, "dflt", None, Reason.ERROR, ErrorCode.FLAG_NOT_FOUND)
    assert result.error_message is not None and "nope" in result.error_message


# --- disabled -------------------------------------------------------------------------------


def test_disabled_serves_off_variation() -> None:
    rs = ruleset(flag("c", enabled=False, off_variation="blue", targets=[]))
    expect(evaluate(rs, "c", {"key": "u"}, "dflt"), "#00f", "blue", Reason.DISABLED)


def test_disabled_without_off_variation_returns_default() -> None:
    rs = ruleset(flag("c", enabled=False, off_variation=None))
    expect(evaluate(rs, "c", {}, "dflt"), "dflt", None, Reason.DISABLED)


def test_disabled_wins_over_targets() -> None:
    rs = ruleset(flag("c", enabled=False, targets=[{"variation": "blue", "values": ["u"]}]))
    expect(evaluate(rs, "c", {"key": "u"}, "dflt"), "#f00", "red", Reason.DISABLED)


# --- prerequisites --------------------------------------------------------------------------


def with_prereq(prereq_flag: str, variation: str = "on", **overrides: Any) -> dict[str, Any]:
    return flag(
        "c",
        prerequisites=[{"flag": prereq_flag, "variation": variation}],
        fallthrough={"variation": "green"},
        **overrides,
    )


def test_prerequisite_met_goes_on() -> None:
    rs = ruleset(with_prereq("g"), gate("g"))
    expect(evaluate(rs, "c", {}, "dflt"), "#0f0", "green", Reason.DEFAULT)


@pytest.mark.parametrize(
    "prereq",
    [
        pytest.param(None, id="missing"),
        pytest.param(gate("g", enabled=False), id="disabled"),
        pytest.param(gate("g", serve="off"), id="wrong-variant"),
    ],
)
def test_prerequisite_failed(prereq: dict[str, Any] | None) -> None:
    flags = [with_prereq("g")] + ([prereq] if prereq else [])
    result = evaluate(ruleset(*flags), "c", {}, "dflt")
    expect(result, "#f00", "red", Reason.PREREQUISITE_FAILED, metadata={"prerequisite": "g"})


def test_prerequisite_failed_without_off_variation_returns_default() -> None:
    rs = ruleset(with_prereq("g", off_variation=None))
    expect(
        evaluate(rs, "c", {}, "dflt"),
        "dflt",
        None,
        Reason.PREREQUISITE_FAILED,
        metadata={"prerequisite": "g"},
    )


def test_prerequisite_whose_off_variation_is_missing_fails() -> None:
    """A disabled prerequisite with no off variation serves no variant, so it fails."""
    rs = ruleset(with_prereq("g", "on"), gate("g", enabled=False, off_variation=None))
    result = evaluate(rs, "c", {}, "dflt")
    assert result.reason == Reason.PREREQUISITE_FAILED


def test_prerequisites_are_checked_in_order() -> None:
    rs = ruleset(
        flag(
            "c",
            prerequisites=[{"flag": "a", "variation": "on"}, {"flag": "b", "variation": "on"}],
        ),
        gate("a", serve="off"),
        gate("b", serve="off"),
    )
    assert evaluate(rs, "c", {}, None).metadata == {"prerequisite": "a"}


def test_prerequisite_is_evaluated_with_the_context() -> None:
    g = gate("g", serve="off", targets=[{"variation": "on", "values": ["vip"]}])
    rs = ruleset(with_prereq("g"), g)
    assert evaluate(rs, "c", {"key": "vip"}, None).variant == "green"
    assert evaluate(rs, "c", {"key": "other"}, None).reason == Reason.PREREQUISITE_FAILED


def test_chained_prerequisites_pass() -> None:
    rs = ruleset(
        with_prereq("g1"),
        gate("g1", prerequisites=[{"flag": "g2", "variation": "on"}]),
        gate("g2"),
    )
    expect(evaluate(rs, "c", {}, "dflt"), "#0f0", "green", Reason.DEFAULT)


def test_chained_prerequisite_failure_fails_the_top() -> None:
    rs = ruleset(
        with_prereq("g1"),
        gate("g1", prerequisites=[{"flag": "g2", "variation": "on"}]),
        gate("g2", serve="off"),
    )
    result = evaluate(rs, "c", {}, "dflt")
    expect(result, "#f00", "red", Reason.PREREQUISITE_FAILED, metadata={"prerequisite": "g1"})


def test_prerequisite_cycle_is_general_error() -> None:
    rs = ruleset(
        gate("a", prerequisites=[{"flag": "b", "variation": "on"}]),
        gate("b", prerequisites=[{"flag": "a", "variation": "on"}]),
    )
    result = evaluate(rs, "a", {}, "dflt")
    expect(result, "dflt", None, Reason.ERROR, ErrorCode.GENERAL)
    assert result.error_message


def test_prerequisite_depth_limit() -> None:
    def chain(length: int) -> Ruleset:
        flags = [
            gate(f"f{i}", prerequisites=[{"flag": f"f{i + 1}", "variation": "on"}])
            for i in range(length)
        ]
        return ruleset(*flags, gate(f"f{length}"))

    assert evaluate(chain(32), "f0", {}, None).variant == "on"
    assert evaluate(chain(33), "f0", {}, None).error_code == ErrorCode.GENERAL


# --- individual targets ---------------------------------------------------------------------


def test_target_match() -> None:
    rs = ruleset(flag("c", targets=[{"variation": "blue", "values": ["u1", "u2"]}]))
    expect(evaluate(rs, "c", {"key": "u2"}, "dflt"), "#00f", "blue", Reason.TARGETING_MATCH)


def test_targets_win_over_rules() -> None:
    rs = ruleset(
        flag(
            "c",
            targets=[{"variation": "blue", "values": ["u1"]}],
            rules=[rule(clause("key", "in", "u1"), serve={"variation": "green"})],
        )
    )
    assert evaluate(rs, "c", {"key": "u1"}, None).variant == "blue"


def test_target_needs_a_string_key() -> None:
    rs = ruleset(flag("c", targets=[{"variation": "blue", "values": ["42"]}]))
    expect(evaluate(rs, "c", {"key": 42}, "dflt"), "#f00", "red", Reason.DEFAULT)


# --- rules ----------------------------------------------------------------------------------


def test_rule_match_has_rule_index() -> None:
    rs = ruleset(
        flag(
            "c",
            rules=[
                rule(clause("plan", "in", "free"), serve={"variation": "blue"}),
                rule(clause("plan", "in", "pro"), serve={"variation": "green"}),
            ],
        )
    )
    expect(
        evaluate(rs, "c", {"plan": "pro"}, "dflt"),
        "#0f0",
        "green",
        Reason.TARGETING_MATCH,
        metadata={"rule_index": 1},
    )


def test_rule_clauses_are_anded() -> None:
    rs = ruleset(
        flag(
            "c",
            rules=[
                rule(
                    clause("plan", "in", "pro"),
                    clause("country", "in", "EG"),
                    serve={"variation": "blue"},
                )
            ],
        )
    )
    assert evaluate(rs, "c", {"plan": "pro", "country": "EG"}, None).variant == "blue"
    assert evaluate(rs, "c", {"plan": "pro", "country": "US"}, None).variant == "red"


def test_first_matching_rule_wins() -> None:
    rs = ruleset(
        flag(
            "c",
            rules=[
                rule(clause("plan", "in", "pro"), serve={"variation": "blue"}),
                rule(clause("plan", "in", "pro"), serve={"variation": "green"}),
            ],
        )
    )
    assert evaluate(rs, "c", {"plan": "pro"}, None).metadata == {"rule_index": 0}


def test_rule_rollout_is_targeting_match_with_rule_index() -> None:
    rs = ruleset(
        flag(
            "c",
            rules=[
                rule(
                    clause("plan", "in", "pro"),
                    serve=rollout("org", ("blue", 50000), ("green", 50000)),
                )
            ],
        )
    )
    result = evaluate(rs, "c", {"plan": "pro", "org": "acme"}, None)
    expected = "blue" if bucket("c", "acme") < 50000 else "green"
    assert (result.variant, result.reason, result.metadata) == (
        expected,
        Reason.TARGETING_MATCH,
        {"rule_index": 0},
    )


@pytest.mark.parametrize("context", [{}, {"plan": None}], ids=["missing", "null"])
def test_missing_or_null_attribute_is_false_even_negated(context: dict[str, Any]) -> None:
    rs = ruleset(
        flag(
            "c", rules=[rule(clause("plan", "in", "pro", negate=True), serve={"variation": "blue"})]
        )
    )
    assert evaluate(rs, "c", context, None).variant == "red"


def test_negate_inverts_a_present_attribute() -> None:
    rs = ruleset(
        flag(
            "c", rules=[rule(clause("plan", "in", "pro", negate=True), serve={"variation": "blue"})]
        )
    )
    assert evaluate(rs, "c", {"plan": "free"}, None).variant == "blue"
    assert evaluate(rs, "c", {"plan": "pro"}, None).variant == "red"


def test_array_attribute_matches_any_element() -> None:
    rs = ruleset(
        flag("c", rules=[rule(clause("groups", "in", "beta"), serve={"variation": "blue"})])
    )
    assert evaluate(rs, "c", {"groups": ["alpha", "beta"]}, None).variant == "blue"
    assert evaluate(rs, "c", {"groups": ["alpha"]}, None).variant == "red"


def test_empty_array_is_present_so_negate_applies() -> None:
    rs = ruleset(
        flag(
            "c",
            rules=[rule(clause("groups", "in", "beta", negate=True), serve={"variation": "blue"})],
        )
    )
    assert evaluate(rs, "c", {"groups": []}, None).variant == "blue"


# --- segments -------------------------------------------------------------------------------


def segment_flag(*, negate: bool = False) -> dict[str, Any]:
    return flag(
        "c",
        rules=[
            rule(
                clause("key", "in_segment", "beta", "other", negate=negate),
                serve={"variation": "blue"},
            )
        ],
    )


def segment_ruleset(*, negate: bool = False) -> Ruleset:
    return ruleset(
        segment_flag(negate=negate),
        segments={
            "beta": segment(
                "beta",
                included=["inc", "both"],
                excluded=["exc", "both"],
                rules=[[clause("plan", "in", "pro")]],
            )
        },
    )


@pytest.mark.parametrize(
    ("context", "variant"),
    [
        pytest.param({"key": "inc"}, "blue", id="included"),
        pytest.param({"key": "exc", "plan": "pro"}, "red", id="excluded-beats-rules"),
        pytest.param({"key": "both"}, "red", id="excluded-beats-included"),
        pytest.param({"key": "x", "plan": "pro"}, "blue", id="rule"),
        pytest.param({"plan": "pro"}, "blue", id="rule-without-key"),
        pytest.param({"key": "x", "plan": "free"}, "red", id="no-match"),
        pytest.param({"key": 1, "plan": "free"}, "red", id="non-string-key"),
    ],
)
def test_segment_precedence(context: dict[str, Any], variant: str) -> None:
    assert evaluate(segment_ruleset(), "c", context, None).variant == variant


def test_segment_non_string_key_skips_lists_but_uses_rules() -> None:
    rs = ruleset(
        segment_flag(),
        segments={"beta": segment("beta", excluded=["1"], rules=[[clause("key", "in", 1)]])},
    )
    assert evaluate(rs, "c", {"key": 1}, None).variant == "blue"


def test_in_segment_negate_applies() -> None:
    rs = segment_ruleset(negate=True)
    assert evaluate(rs, "c", {"key": "inc"}, None).variant == "red"
    assert evaluate(rs, "c", {}, None).variant == "blue"


def test_unknown_segment_never_matches() -> None:
    rs = ruleset(segment_flag())
    assert evaluate(rs, "c", {"key": "inc"}, None).variant == "red"


# --- fallthrough ----------------------------------------------------------------------------


def test_fixed_fallthrough_without_targeting_is_static() -> None:
    expect(evaluate(ruleset(flag("c")), "c", {}, "dflt"), "#f00", "red", Reason.STATIC)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"targets": [{"variation": "blue", "values": ["u"]}]}, id="targets"),
        pytest.param(
            {"rules": [rule(clause("plan", "in", "pro"), serve={"variation": "blue"})]}, id="rules"
        ),
        pytest.param({"prerequisites": [{"flag": "g", "variation": "on"}]}, id="prerequisites"),
    ],
)
def test_fixed_fallthrough_with_targeting_is_default(overrides: dict[str, Any]) -> None:
    rs = ruleset(flag("c", **overrides), gate("g"))
    expect(evaluate(rs, "c", {"key": "other"}, "dflt"), "#f00", "red", Reason.DEFAULT)


def test_rollout_fallthrough_is_split() -> None:
    rs = ruleset(flag("c", fallthrough=rollout("key", ("blue", 50000), ("green", 50000))))
    result = evaluate(rs, "c", {"key": "u1"}, None)
    expected = "blue" if bucket("c", "u1") < 50000 else "green"
    assert (result.variant, result.reason, result.metadata) == (expected, Reason.SPLIT, {})


def test_rollout_uses_the_bucket_by_attribute() -> None:
    weights = (("blue", 50000), ("green", 50000))
    rs = ruleset(flag("c", fallthrough=rollout("org", *weights)))
    keys = [f"org-{i}" for i in range(20)]
    variants = {evaluate(rs, "c", {"key": "same", "org": k}, None).variant for k in keys}
    assert variants == {"blue", "green"}
    for k in keys:
        expected = "blue" if bucket("c", k) < 50000 else "green"
        assert evaluate(rs, "c", {"org": k}, None).variant == expected


def test_rollout_buckets_integral_numbers_as_text() -> None:
    rs = ruleset(flag("c", fallthrough=rollout("id", ("blue", 50000), ("green", 50000))))
    assert (
        evaluate(rs, "c", {"id": 7}, None).variant == evaluate(rs, "c", {"id": "7"}, None).variant
    )
    assert (
        evaluate(rs, "c", {"id": 7.0}, None).variant == evaluate(rs, "c", {"id": 7}, None).variant
    )


@pytest.mark.parametrize("context", [{}, {"key": None}, {"key": True}, {"key": 1.5}])
def test_rollout_without_bucket_value_uses_bucket_zero(context: dict[str, Any]) -> None:
    rs = ruleset(flag("c", fallthrough=rollout("key", ("red", 0), ("blue", 1), ("green", 99999))))
    assert evaluate(rs, "c", context, None).variant == "blue"


# --- values and types -----------------------------------------------------------------------


def test_json_null_variation_value() -> None:
    rs = ruleset(
        flag(
            "c",
            type="json",
            variations=[{"key": "none", "value": None}, {"key": "some", "value": {"a": [1]}}],
            off_variation=None,
            fallthrough={"variation": "none"},
        )
    )
    expect(evaluate(rs, "c", {}, "dflt", "json"), None, "none", Reason.STATIC)


def test_type_mismatch_returns_default() -> None:
    result = evaluate(ruleset(flag("c")), "c", {}, False, "boolean")
    expect(result, False, None, Reason.ERROR, ErrorCode.TYPE_MISMATCH)
    assert result.error_message


@pytest.mark.parametrize("expected_type", ["string", "json"])
def test_matching_type_passes(expected_type: str) -> None:
    result = evaluate(ruleset(flag("c")), "c", {}, "dflt", expected_type)
    expect(result, "#f00", "red", Reason.STATIC)


def test_number_type_refuses_bool() -> None:
    rs = ruleset(gate("g"))
    assert evaluate(rs, "g", {}, 0, "number").error_code == ErrorCode.TYPE_MISMATCH


def test_type_is_not_checked_when_no_variation_is_served() -> None:
    rs = ruleset(flag("c", enabled=False, off_variation=None))
    expect(evaluate(rs, "c", {}, 123, "boolean"), 123, None, Reason.DISABLED)


def test_type_is_not_checked_on_errors() -> None:
    result = evaluate(ruleset(flag("c")), "nope", {}, 123, "boolean")
    assert result.error_code == ErrorCode.FLAG_NOT_FOUND


def test_metadata_is_not_shared_between_results() -> None:
    rs = ruleset(flag("c", rules=[rule(clause("plan", "in", "pro"), serve={"variation": "blue"})]))
    first = evaluate(rs, "c", {"plan": "pro"}, None)
    first.metadata["x"] = 1
    assert evaluate(rs, "c", {"plan": "pro"}, None).metadata == {"rule_index": 0}


# --- evaluate_all ---------------------------------------------------------------------------


def test_evaluate_all_defaults_to_every_flag_sorted() -> None:
    rs = ruleset(flag("zeta"), gate("alpha"), flag("mid", enabled=False))
    results = evaluate_all(rs, {})
    assert list(results) == ["alpha", "mid", "zeta"]
    assert results["alpha"].variant == "on"
    assert results["mid"].reason == Reason.DISABLED


def test_evaluate_all_with_keys_sorts_and_reports_unknown() -> None:
    rs = ruleset(flag("zeta"), gate("alpha"))
    results = evaluate_all(rs, {}, ["zeta", "nope", "zeta"])
    assert list(results) == ["nope", "zeta"]
    expect(results["nope"], None, None, Reason.ERROR, ErrorCode.FLAG_NOT_FOUND)
    assert results["zeta"].variant == "red"


def test_evaluate_all_invalid_context() -> None:
    results = evaluate_all(ruleset(flag("c")), [])  # type: ignore[arg-type]
    assert results["c"].error_code == ErrorCode.INVALID_CONTEXT


# --- performance ----------------------------------------------------------------------------


def test_performance() -> None:
    rules = [
        rule(
            clause("email", "matches", rf"^user{i}@example\.com$"),
            clause("version", "semver_gt", "1.2.3"),
            clause("country", "in", "EG", "US", "DE"),
            serve={"variation": "blue"},
        )
        for i in range(20)
    ]
    rs = ruleset(flag("c", rules=rules))
    context = {"email": "user19@example.com", "version": "2.0.0", "country": "DE"}
    assert evaluate(rs, "c", context, None).metadata == {"rule_index": 19}

    start = time.perf_counter()
    for _ in range(10_000):
        evaluate(rs, "c", context, None)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"10000 evaluations took {elapsed:.3f}s"

"""Ruleset builders for the engine tests and the p99 guard (the Go SDK's genRuleset)."""

import json
from typing import Any

Json = dict[str, Any]

BENCH_SEGMENTS = 50


def flag_json(key: str, **fields: Any) -> Json:
    """An enabled string flag with variations on and off, off_variation off and fallthrough on."""
    return {
        "key": key,
        "type": "string",
        "enabled": True,
        "version": 1,
        "variations": [{"key": "on", "value": "on"}, {"key": "off", "value": "off"}],
        "off_variation": "off",
        "prerequisites": [],
        "targets": [],
        "rules": [],
        "fallthrough": {"variation": "on"},
        **fields,
    }


def cl(attribute: str, operator: str, negate: bool, *values: Any) -> Json:
    return {"attribute": attribute, "operator": operator, "negate": negate, "values": list(values)}


def ruleset_json(flags: list[Json], segments: Json | None = None) -> Json:
    return {
        "schema_version": 1,
        "project": "p",
        "environment": "e",
        "flags": {f["key"]: f for f in flags},
        "segments": segments or {},
    }


def flag_keys(n: int) -> list[str]:
    return [f"flag-{i}" for i in range(n)]


def _bench_rule(variation: str, *clauses: Json) -> Json:
    return {"description": "", "clauses": list(clauses), "serve": {"variation": variation}}


def gen_ruleset(n: int) -> Json:
    """A deterministic ruleset of n flags and 50 segments.

    Even flags are booleans with three rules (in, matches, semver_gt), a 100-key target list and a
    three-way fallthrough rollout; odd flags are strings with an in_segment rule and a before rule.
    Every 10th flag has a prerequisite on the flag before it.
    """
    plans = [f"plan-{i}" for i in range(20)]
    flags: list[Json] = []
    for i, key in enumerate(flag_keys(n)):
        if i % 2 == 0:
            f = flag_json(
                key,
                type="boolean",
                variations=[
                    {"key": "on", "value": True},
                    {"key": "off", "value": False},
                    {"key": "alt", "value": False},
                ],
                targets=[
                    {
                        "variation": "alt",
                        "values": [f"user-{(i + k * 37) % 1000}" for k in range(100)],
                    }
                ],
                rules=[
                    _bench_rule("on", cl("plan", "in", False, *plans)),
                    _bench_rule("off", cl("email", "matches", False, "^user-[0-9]+$")),
                    _bench_rule("alt", cl("app_version", "semver_gt", False, "1.5.0")),
                ],
                fallthrough={
                    "rollout": {
                        "bucket_by": "key",
                        "weights": [
                            {"variation": "on", "weight": 33334},
                            {"variation": "off", "weight": 33333},
                            {"variation": "alt", "weight": 33333},
                        ],
                    }
                },
            )
        else:
            f = flag_json(
                key,
                variations=[
                    {"key": "on", "value": "on"},
                    {"key": "off", "value": "off"},
                    {"key": "alt", "value": "alt"},
                ],
                rules=[
                    _bench_rule(
                        "alt", cl("key", "in_segment", False, f"segment-{i % BENCH_SEGMENTS}")
                    ),
                    _bench_rule("off", cl("signed_up", "before", False, "2025-01-01T00:00:00Z")),
                ],
            )
        if i > 0 and i % 10 == 0:
            f["prerequisites"] = [{"flag": f"flag-{i - 1}", "variation": "on"}]
        flags.append(f)
    segments = {
        f"segment-{s}": {
            "included": [f"user-{s * 1000 + k}" for k in range(1000)],
            "excluded": [],
            "rules": [{"clauses": [cl("org", "in", False, f"org-{s}")]}],
        }
        for s in range(BENCH_SEGMENTS)
    }
    return {**ruleset_json(flags, segments), "project": "bench", "environment": "bench"}


def bench_contexts() -> list[Json]:
    """100 contexts that hit every rule shape of gen_ruleset."""
    return [
        {
            "key": f"user-{j * 503}",
            "plan": f"plan-{j % 40}",
            "email": f"user-{j}" if j % 3 == 0 else f"user{j}@example.com",
            "app_version": f"1.{j % 10}.0",
            "org": f"org-{j % 100}",
        }
        for j in range(100)
    ]


_BOOL_VARIATIONS = [{"key": "on", "value": True}, {"key": "off", "value": False}]


def _provider_flags() -> list[Json]:
    """The provider tests' flags: one of each type, a target, a rule and a failing prerequisite
    (the Go SDK's testdata/ruleset.json, plus an integral number flag)."""
    return [
        flag_json(
            "bool-flag",
            type="boolean",
            variations=_BOOL_VARIATIONS,
            rules=[
                {
                    "description": "pro plan",
                    "clauses": [cl("plan", "in", False, "pro")],
                    "serve": {"variation": "on"},
                }
            ],
            fallthrough={"variation": "off"},
        ),
        flag_json(
            "targeted",
            variations=[
                {"key": "alice", "value": "hello alice"},
                {"key": "other", "value": "hello"},
            ],
            off_variation="other",
            targets=[{"variation": "alice", "values": ["alice"]}],
            fallthrough={"variation": "other"},
        ),
        flag_json(
            "string-flag",
            variations=[{"key": "blue", "value": "blue"}, {"key": "red", "value": "red"}],
            off_variation="red",
            fallthrough={"variation": "blue"},
        ),
        flag_json(
            "number-flag",
            type="number",
            variations=[{"key": "half", "value": 2.5}, {"key": "zero", "value": 0}],
            off_variation="zero",
            fallthrough={"variation": "half"},
        ),
        flag_json(
            "int-flag",
            type="number",
            variations=[{"key": "seven", "value": 7}, {"key": "zero", "value": 0}],
            off_variation="zero",
            fallthrough={"variation": "seven"},
        ),
        flag_json(
            "json-flag",
            type="json",
            variations=[
                {"key": "cfg", "value": {"limit": 10, "tags": ["a", "b"]}},
                {"key": "empty", "value": {}},
            ],
            off_variation="empty",
            fallthrough={"variation": "cfg"},
        ),
        flag_json("prereq-off", type="boolean", enabled=False, variations=_BOOL_VARIATIONS),
        flag_json(
            "needs-prereq",
            type="boolean",
            variations=_BOOL_VARIATIONS,
            prerequisites=[{"flag": "prereq-off", "variation": "on"}],
        ),
    ]


def provider_ruleset() -> str:
    """The provider tests' ruleset as JSON text."""
    return json.dumps(ruleset_json(_provider_flags()))


def changed_provider_ruleset() -> str:
    """Serves red for string-flag (version 2), removes targeted and adds new-flag."""
    flags = [f for f in _provider_flags() if f["key"] != "targeted"]
    for f in flags:
        if f["key"] == "string-flag":
            f.update(version=2, fallthrough={"variation": "red"})
            flags.append({**f, "key": "new-flag"})
            break
    return json.dumps(ruleset_json(flags))

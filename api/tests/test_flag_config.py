import copy
from typing import Any

import httpx
from fastapi import FastAPI
from helpers import assert_error, count_statements, create_flag, create_project
from sqlalchemy import text

FLAG = "/api/projects/demo/flags/new-ui"

# Three rules with two clauses each on new-ui/production: two serve `true`, the last a rollout.
SEED_RULES = (
    "WITH fe AS ("
    "  SELECT fe.id FROM flag_environment fe"
    "  JOIN flag f ON f.id = fe.flag_id AND f.key = 'new-ui'"
    "  JOIN environment e ON e.id = fe.environment_id AND e.key = 'production'"
    "), v AS ("
    "  SELECT v.key, v.id FROM variation v"
    "  JOIN flag f ON f.id = v.flag_id AND f.key = 'new-ui'"
    "), r AS ("
    "  INSERT INTO flag_rule (id, flag_environment_id, position, description, variation_id,"
    "                         bucket_by)"
    "  SELECT gen_random_uuid(), fe.id, n, 'rule ' || n,"
    "         CASE WHEN n < 2 THEN (SELECT id FROM v WHERE key = 'true') END,"
    "         CASE WHEN n = 2 THEN 'org' END"
    "  FROM fe, generate_series(0, 2) n"
    "  RETURNING id, position"
    "), w AS ("
    "  INSERT INTO rollout_weight (id, flag_rule_id, variation_id, weight)"
    "  SELECT gen_random_uuid(), r.id, v.id, CASE v.key WHEN 'true' THEN 25000 ELSE 75000 END"
    "  FROM r, v WHERE r.position = 2"
    ")"
    " INSERT INTO clause (id, flag_rule_id, position, attribute, operator, values)"
    " SELECT gen_random_uuid(), r.id, c, 'attr' || c, 'in', '[\"x\"]'::jsonb"
    " FROM r, generate_series(0, 1) c"
)


async def test_list_env_summaries(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    r = await api.get(f"{FLAG}/environments")

    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["environment"] for s in body] == ["development", "staging", "production"]
    assert all(s["enabled"] is False and s["version"] == 1 for s in body)
    assert all(set(s) == {"environment", "enabled", "version", "updated_at"} for s in body)


async def test_get_default_config(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    r = await api.get(f"{FLAG}/environments/production")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.pop("updated_at")
    assert body == {
        "environment": "production",
        "enabled": False,
        "version": 1,
        "targets": [],
        "rules": [],
        "fallthrough": {"variation": "true", "rollout": None},
        "prerequisites": [],
    }


async def test_unknown_env_404(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    assert_error(await api.get(f"{FLAG}/environments/nope"), 404, "not_found")


async def test_unknown_flag_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    assert_error(await api.get(f"{FLAG}/environments"), 404, "not_found")
    assert_error(await api.get(f"{FLAG}/environments/production"), 404, "not_found")


async def test_get_config_constant_queries(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    await create_flag(api, key="empty")
    async with app.state.engine.begin() as conn:
        await conn.execute(text(SEED_RULES))

    with count_statements(app.state.engine) as empty_statements:
        empty = await api.get("/api/projects/demo/flags/empty/environments/production")
    with count_statements(app.state.engine) as statements:
        r = await api.get(f"{FLAG}/environments/production")

    assert empty.status_code == 200, empty.text
    assert r.status_code == 200, r.text
    assert len(statements) == len(empty_statements)
    rules = r.json()["rules"]
    assert [rule["description"] for rule in rules] == ["rule 0", "rule 1", "rule 2"]
    assert [rule["variation"] for rule in rules] == ["true", "true", None]
    assert rules[2]["rollout"] == {
        "bucket_by": "org",
        "weights": [
            {"variation": "true", "weight": 25000},
            {"variation": "false", "weight": 75000},
        ],
    }
    assert all(
        rule["clauses"]
        == [
            {"attribute": "attr0", "operator": "in", "values": ["x"], "negate": False},
            {"attribute": "attr1", "operator": "in", "values": ["x"], "negate": False},
        ]
        for rule in rules
    )


COLOR = "/api/projects/demo/flags/color"
PRODUCTION = f"{COLOR}/environments/production"
FULL_CONFIG: dict[str, Any] = {
    "version": 1,
    "enabled": True,
    "targets": [{"variation": "green", "values": ["u2", "u1"]}],
    "rules": [
        {
            "clauses": [{"attribute": "country", "operator": "in", "values": ["EG"]}],
            "variation": "blue",
        },
        {
            "clauses": [{"operator": "in_segment", "values": ["beta"]}],
            "rollout": {
                "bucket_by": "org",
                "weights": [
                    {"variation": "red", "weight": 50000},
                    {"variation": "green", "weight": 50000},
                ],
            },
        },
    ],
    "fallthrough": {"rollout": {"weights": [{"variation": "blue", "weight": 100000}]}},
}


async def setup_color(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(
        api,
        key="color",
        type="string",
        variations=[{"key": key, "value": key} for key in ("red", "green", "blue")],
        off_variation="red",
    )
    r = await api.post("/api/projects/demo/segments", json={"key": "beta", "name": "Beta"})
    assert r.status_code == 201, r.text


def config(**changes: Any) -> dict[str, Any]:
    return copy.deepcopy(FULL_CONFIG) | changes


async def test_put_full_config_roundtrip(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.put(PRODUCTION, json=FULL_CONFIG)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 2
    assert body["enabled"] is True
    assert body["targets"] == [{"variation": "green", "values": ["u1", "u2"]}]
    assert body["rules"] == [
        {
            "variation": "blue",
            "rollout": None,
            "description": "",
            "clauses": [
                {"attribute": "country", "operator": "in", "values": ["EG"], "negate": False}
            ],
        },
        {
            "variation": None,
            "rollout": FULL_CONFIG["rules"][1]["rollout"],
            "description": "",
            "clauses": [
                {"attribute": "key", "operator": "in_segment", "values": ["beta"], "negate": False}
            ],
        },
    ]
    assert body["fallthrough"] == {
        "variation": None,
        "rollout": {"bucket_by": "key", "weights": [{"variation": "blue", "weight": 100000}]},
    }
    assert body["prerequisites"] == []
    again = await api.get(PRODUCTION)
    assert again.status_code == 200, again.text
    assert again.json() == body


async def test_put_replaces_previous(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_color(api)
    assert (await api.put(PRODUCTION, json=FULL_CONFIG)).status_code == 200

    r = await api.put(
        PRODUCTION, json=config(version=2, targets=[], rules=[], fallthrough={"variation": "green"})
    )

    assert r.status_code == 200, r.text
    assert r.json()["version"] == 3
    assert r.json()["fallthrough"] == {"variation": "green", "rollout": None}
    async with app.state.engine.connect() as conn:
        for table in ("flag_target", "flag_rule", "rollout_weight", "clause", "clause_segment"):
            count = await conn.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            assert count == 0, table
        bucket_by = await conn.scalar(
            text(
                "SELECT fe.fallthrough_bucket_by FROM flag_environment fe"
                " JOIN environment e ON e.id = fe.environment_id AND e.key = 'production'"
            )
        )
        assert bucket_by is None


async def test_put_stale_version_409(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    assert (await api.put(PRODUCTION, json=FULL_CONFIG)).status_code == 200

    body = assert_error(await api.put(PRODUCTION, json=FULL_CONFIG), 409, "version_conflict")

    assert body["details"] == {"version": 2}


async def test_put_unknown_variation_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    body = config()
    body["rules"][0]["variation"] = "purple"

    error = assert_error(await api.put(PRODUCTION, json=body), 422, "invalid_reference")

    assert error["details"] == {"path": "rules[0].variation"}


async def test_put_unknown_segment_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    body = config()
    body["rules"][1]["clauses"][0]["values"] = ["gamma"]

    error = assert_error(await api.put(PRODUCTION, json=body), 422, "invalid_reference")

    assert error["details"] == {"path": "rules[1].clauses[0].values[0]"}


async def test_put_weights_sum_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    body = config()
    body["rules"][1]["rollout"]["weights"][1]["weight"] = 40000

    error = assert_error(await api.put(PRODUCTION, json=body), 422, "validation_error")

    assert error["details"] == {"path": "rules[1].rollout.weights"}


async def test_put_duplicate_weight_variation_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    weights = [{"variation": "blue", "weight": 50000}, {"variation": "blue", "weight": 50000}]

    r = await api.put(PRODUCTION, json=config(fallthrough={"rollout": {"weights": weights}}))

    error = assert_error(r, 422, "validation_error")
    assert error["details"] == {"path": "fallthrough.rollout.weights[1].variation"}


async def test_put_rule_with_both_variation_and_rollout_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    body = config()
    body["rules"][1]["variation"] = "blue"

    error = assert_error(await api.put(PRODUCTION, json=body), 422, "validation_error")

    assert [(d["loc"], "exactly one" in d["msg"]) for d in error["details"]] == [
        (["body", "rules", 1], True)
    ]


async def test_put_context_key_in_two_lists_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    targets = [
        {"variation": "green", "values": ["u1", "u2"]},
        {"variation": "blue", "values": ["u3", "u2"]},
    ]

    error = assert_error(
        await api.put(PRODUCTION, json=config(targets=targets)), 422, "validation_error"
    )

    assert error["details"] == {"path": "targets[1].values[1]"}


async def test_put_bad_operator_value_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    body = config()
    body["rules"][0]["clauses"].append({"attribute": "age", "operator": "gt", "values": ["old"]})

    error = assert_error(await api.put(PRODUCTION, json=body), 422, "validation_error")

    assert error["details"] == {"path": "rules[0].clauses[1].values[0]"}


async def test_put_prerequisites_not_supported_yet_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    prerequisites = [{"flag": "other", "variation": "on"}]

    r = await api.put(PRODUCTION, json=config(prerequisites=prerequisites))

    error = assert_error(r, 422, "validation_error")
    assert error["details"] == {"path": "prerequisites"}


async def test_put_failed_validation_keeps_config(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    saved = (await api.put(PRODUCTION, json=FULL_CONFIG)).json()
    body = config(version=2)
    body["rules"][1]["clauses"][0]["values"] = ["gamma"]

    assert_error(await api.put(PRODUCTION, json=body), 422, "invalid_reference")

    assert (await api.get(PRODUCTION)).json() == saved


async def test_put_other_envs_untouched(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    assert (await api.put(PRODUCTION, json=FULL_CONFIG)).status_code == 200

    development = (await api.get(f"{COLOR}/environments/development")).json()
    assert development["version"] == 1
    assert development["enabled"] is False
    assert development["targets"] == development["rules"] == []


async def test_put_unknown_env_404(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    assert_error(await api.put(f"{COLOR}/environments/nope", json=FULL_CONFIG), 404, "not_found")


async def test_config_variations_and_segment_in_use(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    assert (await api.put(PRODUCTION, json=FULL_CONFIG)).status_code == 200

    blue = assert_error(await api.delete(f"{COLOR}/variations/blue"), 409, "in_use")
    green = assert_error(await api.delete(f"{COLOR}/variations/green"), 409, "in_use")
    beta = assert_error(await api.delete("/api/projects/demo/segments/beta"), 409, "in_use")

    in_production = {"flag": "color", "environment": "production"}
    assert {"usage": "rules", **in_production} in blue["details"]["used_by"]
    assert {"usage": "rollouts", **in_production} in blue["details"]["used_by"]
    assert {"usage": "targets", **in_production} in green["details"]["used_by"]
    assert {"usage": "rollouts", **in_production} in green["details"]["used_by"]
    assert beta["details"]["used_by"] == [in_production]

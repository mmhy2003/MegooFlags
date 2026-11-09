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

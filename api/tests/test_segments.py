from typing import Any

import httpx
from fastapi import FastAPI
from helpers import assert_error, create_flag, create_project
from sqlalchemy import text

SEGMENTS = "/api/projects/demo/segments"
BETA = f"{SEGMENTS}/beta"
PRO_RULE = {
    "description": "pro",
    "clauses": [{"attribute": "plan", "operator": "in", "values": ["pro"]}],
}


async def create_segment(api: httpx.AsyncClient, key: str = "beta", **extra: Any) -> dict[str, Any]:
    r = await api.post(SEGMENTS, json={"key": key, "name": key.title(), **extra})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_create_201(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.post(SEGMENTS, json={"key": "beta", "name": "Beta"})

    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["key"], body["name"], body["description"]) == ("beta", "Beta", "")
    assert (body["included"], body["excluded"], body["rules"]) == ([], [], [])
    assert body["created_at"] and body["updated_at"]
    assert_error(
        await api.post(SEGMENTS, json={"key": "beta", "name": "Again"}), 409, "key_conflict"
    )


async def test_create_in_unknown_project_404(api: httpx.AsyncClient) -> None:
    r = await api.post("/api/projects/nope/segments", json={"key": "beta", "name": "Beta"})

    assert_error(r, 404, "not_found")


async def test_list_paginated(api: httpx.AsyncClient) -> None:
    await create_project(api)
    for key in ("c-seg", "a-seg", "b-seg"):
        await create_segment(api, key)

    first = (await api.get(SEGMENTS, params={"limit": 2})).json()
    second = (await api.get(SEGMENTS, params={"limit": 2, "cursor": first["next_cursor"]})).json()

    assert [s["key"] for s in first["items"]] == ["a-seg", "b-seg"]
    assert set(first["items"][0]) == {"key", "name", "description", "created_at", "updated_at"}
    assert [s["key"] for s in second["items"]] == ["c-seg"]
    assert second["next_cursor"] is None


async def test_list_q_filters_key_or_name(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_segment(api, "beta")
    await create_segment(api, "internal")
    await api.patch(f"{SEGMENTS}/internal", json={"name": "Staff Testers"})

    by_key = (await api.get(SEGMENTS, params={"q": "BET"})).json()
    by_name = (await api.get(SEGMENTS, params={"q": "testers"})).json()
    literal = (await api.get(SEGMENTS, params={"q": "%"})).json()

    assert [s["key"] for s in by_key["items"]] == ["beta"]
    assert [s["key"] for s in by_name["items"]] == ["internal"]
    assert literal["items"] == []


async def test_get_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    assert_error(await api.get(f"{SEGMENTS}/nope"), 404, "not_found")
    assert_error(await api.put(f"{SEGMENTS}/nope", json={}), 404, "not_found")
    assert_error(await api.patch(f"{SEGMENTS}/nope", json={"name": "X"}), 404, "not_found")
    assert_error(await api.delete(f"{SEGMENTS}/nope"), 404, "not_found")


async def test_patch_name_description(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_segment(api)

    r = await api.patch(BETA, json={"name": "Beta testers", "description": "Early access"})
    partial = await api.patch(BETA, json={"description": "Opt-in"})

    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["description"]) == ("Beta testers", "Early access")
    assert (partial.json()["name"], partial.json()["description"]) == ("Beta testers", "Opt-in")
    assert (await api.get(BETA)).json()["name"] == "Beta testers"


async def test_put_replaces_config(api: httpx.AsyncClient) -> None:
    await create_project(api)
    created = await create_segment(api)

    r = await api.put(
        BETA, json={"included": ["u2", "u1", "u2"], "excluded": ["u3"], "rules": [PRO_RULE]}
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["included"] == ["u1", "u2"]
    assert body["excluded"] == ["u3"]
    assert body["rules"] == [
        {
            "description": "pro",
            "clauses": [
                {"attribute": "plan", "operator": "in", "values": ["pro"], "negate": False}
            ],
        }
    ]
    assert body["updated_at"] > created["updated_at"]
    assert (await api.get(BETA)).json() == body

    r = await api.put(BETA, json={"included": ["u9"], "rules": []})

    assert r.status_code == 200, r.text
    assert (r.json()["included"], r.json()["excluded"], r.json()["rules"]) == (["u9"], [], [])
    assert (await api.get(BETA)).json() == r.json()


async def test_put_keeps_rule_order(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_segment(api)
    rules = [
        {"clauses": [{"operator": "in", "values": ["b"]}]},
        {"clauses": [{"operator": "in", "values": ["a"]}, {"operator": "gt", "values": [1]}]},
    ]

    r = await api.put(BETA, json={"rules": rules})

    assert r.status_code == 200, r.text
    assert [[c["values"] for c in rule["clauses"]] for rule in r.json()["rules"]] == [
        [["b"]],
        [["a"], [1]],
    ]
    assert r.json()["rules"][0]["clauses"][0]["attribute"] == "key"


async def test_put_key_in_both_lists_422(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_segment(api)

    r = await api.put(BETA, json={"included": ["u1", "u2"], "excluded": ["u2"]})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "excluded[0]"}


async def test_put_in_segment_rule_422(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_segment(api)
    rule = {"clauses": [{"operator": "in_segment", "values": ["beta"]}]}

    r = await api.put(BETA, json={"rules": [rule]})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "rules[0].clauses[0].operator"}


async def test_put_bad_operator_values_422(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_segment(api)
    rule = {"clauses": [PRO_RULE["clauses"][0], {"operator": "lt", "values": [1, "x"]}]}

    r = await api.put(BETA, json={"included": ["u1"], "rules": [PRO_RULE, rule]})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "rules[1].clauses[1].values[1]"}
    assert (await api.get(BETA)).json()["included"] == []


async def test_delete_unused_204(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_segment(api)
    await api.put(BETA, json={"included": ["u1"], "rules": [PRO_RULE]})

    r = await api.delete(BETA)

    assert r.status_code == 204, r.text
    assert_error(await api.get(BETA), 404, "not_found")
    await create_segment(api)


async def test_delete_in_use_409(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api, key="new-ui")
    await create_segment(api)
    async with app.state.engine.begin() as conn:
        await conn.execute(
            text(
                "WITH fe AS ("
                "  SELECT fe.id FROM flag_environment fe"
                "  JOIN flag f ON f.id = fe.flag_id AND f.key = 'new-ui'"
                "  JOIN environment e ON e.id = fe.environment_id AND e.key = 'production'"
                "), r AS ("
                "  INSERT INTO flag_rule (id, flag_environment_id, position, variation_id)"
                "  SELECT gen_random_uuid(), fe.id, 0, v.id FROM fe"
                "  JOIN variation v ON v.key = 'true'"
                "  JOIN flag f ON f.id = v.flag_id AND f.key = 'new-ui'"
                "  RETURNING id"
                "), c AS ("
                "  INSERT INTO clause (id, flag_rule_id, position, attribute, operator)"
                "  SELECT gen_random_uuid(), r.id, 0, 'key', 'in_segment' FROM r"
                "  RETURNING id"
                ")"
                " INSERT INTO clause_segment (clause_id, segment_id)"
                " SELECT c.id, s.id FROM c, segment s WHERE s.key = 'beta'"
            )
        )

    body = assert_error(await api.delete(BETA), 409, "in_use")

    assert body["details"] == {"used_by": [{"flag": "new-ui", "environment": "production"}]}
    assert (await api.get(BETA)).status_code == 200

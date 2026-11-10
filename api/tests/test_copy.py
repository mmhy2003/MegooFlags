from typing import Any

import httpx
from fastapi import FastAPI
from helpers import assert_error, create_flag, create_project
from sqlalchemy import text

FLAGS = "/api/projects/demo/flags"
COLOR = f"{FLAGS}/color"
COPY = f"{COLOR}/copy"
STAGING_CONFIG: dict[str, Any] = {
    "version": 1,
    "enabled": True,
    "targets": [{"variation": "green", "values": ["u1", "u2"]}],
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
    "fallthrough": {
        "rollout": {
            "bucket_by": "team",
            "weights": [
                {"variation": "green", "weight": 30000},
                {"variation": "blue", "weight": 70000},
            ],
        }
    },
    "prerequisites": [{"flag": "gate", "variation": "true"}],
}
COPIED = ("targets", "rules", "fallthrough", "prerequisites")


async def setup_color(api: httpx.AsyncClient) -> None:
    """color with the full config on staging, which needs the boolean flag gate."""
    await create_project(api)
    await create_flag(api, key="gate")
    await create_flag(
        api,
        key="color",
        type="string",
        variations=[{"key": key, "value": key} for key in ("red", "green", "blue")],
        off_variation="red",
    )
    r = await api.post("/api/projects/demo/segments", json={"key": "beta", "name": "Beta"})
    assert r.status_code == 201, r.text
    r = await api.put(f"{COLOR}/environments/staging", json=STAGING_CONFIG)
    assert r.status_code == 200, r.text


async def get_config(api: httpx.AsyncClient, env: str, flag: str = "color") -> dict[str, Any]:
    r = await api.get(f"{FLAGS}/{flag}/environments/{env}")
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_copy_to_production(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    staging = await get_config(api, "staging")

    r = await api.post(COPY, json={"from": "staging", "to": ["production"]})

    assert r.status_code == 200, r.text
    [state] = r.json()
    assert state["environment"] == "production"
    assert state["enabled"] is False
    assert state["version"] == 2
    production = await get_config(api, "production")
    assert {k: production[k] for k in COPIED} == {k: staging[k] for k in COPIED}
    assert production["enabled"] is False
    assert production["version"] == 2
    assert await get_config(api, "staging") == staging


async def test_copy_include_enabled(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.post(
        COPY, json={"from": "staging", "to": ["production"], "include_enabled": True}
    )

    assert r.status_code == 200, r.text
    assert r.json()[0]["enabled"] is True
    assert (await get_config(api, "production"))["enabled"] is True


async def test_copy_to_many(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    staging = await get_config(api, "staging")

    r = await api.post(COPY, json={"from": "staging", "to": ["production", "development"]})

    assert r.status_code == 200, r.text
    assert [s["environment"] for s in r.json()] == ["development", "production"]
    assert all(s["version"] == 2 for s in r.json())
    for env in ("development", "production"):
        got = await get_config(api, env)
        assert {k: got[k] for k in COPIED} == {k: staging[k] for k in COPIED}


async def test_copy_replaces_target_rows(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_color(api)
    other = {
        "version": 1,
        "enabled": False,
        "targets": [{"variation": "red", "values": ["x1", "x2", "x3"]}],
        "rules": [
            {
                "clauses": [{"attribute": "plan", "operator": "in", "values": ["pro"]}],
                "variation": v,
            }
            for v in ("red", "green", "blue")
        ],
        "fallthrough": {"variation": "red"},
    }
    assert (await api.put(f"{COLOR}/environments/production", json=other)).status_code == 200

    r = await api.post(COPY, json={"from": "staging", "to": ["production"]})

    assert r.status_code == 200, r.text
    counts = (
        "SELECT e.key,"
        " (SELECT count(*) FROM flag_target t WHERE t.flag_environment_id = fe.id),"
        " (SELECT count(*) FROM flag_rule r WHERE r.flag_environment_id = fe.id),"
        " (SELECT count(*) FROM clause c JOIN flag_rule r ON r.id = c.flag_rule_id"
        "  WHERE r.flag_environment_id = fe.id)"
        " FROM flag_environment fe"
        " JOIN flag f ON f.id = fe.flag_id AND f.key = 'color'"
        " JOIN environment e ON e.id = fe.environment_id"
        " WHERE e.key IN ('staging', 'production')"
    )
    async with app.state.engine.connect() as conn:
        rows = {key: tuple(rest) for key, *rest in await conn.execute(text(counts))}
    assert rows["production"] == rows["staging"] == (2, 2, 2)


async def test_copy_to_self_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.post(COPY, json={"from": "staging", "to": ["production", "staging"]})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "to"}


async def test_copy_duplicate_targets_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.post(COPY, json={"from": "staging", "to": ["production", "production"]})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "to"}


async def test_copy_empty_to_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    assert_error(await api.post(COPY, json={"from": "staging", "to": []}), 422, "validation_error")


async def test_copy_unknown_env_404(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    for body in ({"from": "nope", "to": ["production"]}, {"from": "staging", "to": ["nope"]}):
        error = assert_error(await api.post(COPY, json=body), 404, "not_found")
        assert error["message"] == "environment 'nope' not found"


async def test_copy_unknown_flag_404(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.post(f"{FLAGS}/nope/copy", json={"from": "staging", "to": ["production"]})

    assert_error(r, 404, "not_found")


async def test_copy_cycle_422_rolls_back(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    gate = await get_config(api, "production", "gate")
    r = await api.put(
        f"{FLAGS}/gate/environments/production",
        json={
            "version": gate["version"],
            "enabled": gate["enabled"],
            "fallthrough": gate["fallthrough"],
            "prerequisites": [{"flag": "color", "variation": "red"}],
        },
    )
    assert r.status_code == 200, r.text
    before = await get_config(api, "development")

    r = await api.post(COPY, json={"from": "staging", "to": ["development", "production"]})

    assert_error(r, 422, "prerequisite_cycle")
    production = await get_config(api, "production")
    assert production["version"] == 1
    assert production["prerequisites"] == []
    assert await get_config(api, "development") == before

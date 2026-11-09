from typing import Any

import httpx
from helpers import assert_error, create_flag, create_project

PRODUCTION = "/api/projects/demo/flags/new-ui/environments/production"
TOGGLE = f"{PRODUCTION}/toggle"
KILL = f"{PRODUCTION}/kill"
RULES: list[dict[str, Any]] = [
    {"clauses": [{"attribute": "country", "operator": "in", "values": ["EG"]}], "variation": "true"}
]


async def setup_flag(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)


async def turn_on(api: httpx.AsyncClient) -> dict[str, Any]:
    r = await api.post(TOGGLE, json={"enabled": True})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_toggle_on(api: httpx.AsyncClient) -> None:
    await setup_flag(api)

    r = await api.post(TOGGLE, json={"enabled": True})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body.pop("updated_at")
    assert body == {"environment": "production", "enabled": True, "version": 2}


async def test_toggle_same_state_no_bump(api: httpx.AsyncClient) -> None:
    await setup_flag(api)
    first = await turn_on(api)

    second = await turn_on(api)

    assert second == first
    assert second["version"] == 2


async def test_toggle_with_stale_version_409(api: httpx.AsyncClient) -> None:
    await setup_flag(api)
    await turn_on(api)

    body = assert_error(
        await api.post(TOGGLE, json={"enabled": False, "version": 1}), 409, "version_conflict"
    )

    assert body["details"] == {"version": 2}
    assert (await api.get(PRODUCTION)).json()["enabled"] is True


async def test_toggle_with_current_version_ok(api: httpx.AsyncClient) -> None:
    await setup_flag(api)
    await turn_on(api)

    r = await api.post(TOGGLE, json={"enabled": False, "version": 2})

    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False
    assert r.json()["version"] == 3


async def test_kill_turns_off(api: httpx.AsyncClient) -> None:
    await setup_flag(api)
    await turn_on(api)

    r = await api.post(KILL)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False
    assert body["version"] == 3
    assert set(body) == {"environment", "enabled", "version", "updated_at"}


async def test_kill_wins_over_stale_version(api: httpx.AsyncClient) -> None:
    await setup_flag(api)
    await turn_on(api)
    read = (await api.get(PRODUCTION)).json()

    killed = await api.post(KILL)

    assert killed.status_code == 200, killed.text
    assert killed.json()["version"] == read["version"] + 1
    stale = {k: read[k] for k in ("version", "enabled", "targets", "rules", "fallthrough")}
    body = assert_error(await api.put(PRODUCTION, json=stale), 409, "version_conflict")
    assert body["details"] == {"version": read["version"] + 1}
    assert (await api.get(PRODUCTION)).json()["enabled"] is False


async def test_kill_idempotent(api: httpx.AsyncClient) -> None:
    await setup_flag(api)
    await turn_on(api)
    first = (await api.post(KILL)).json()

    r = await api.post(KILL)

    assert r.status_code == 200, r.text
    assert r.json() == first
    assert r.json()["enabled"] is False


async def test_kill_leaves_targeting(api: httpx.AsyncClient) -> None:
    await setup_flag(api)
    r = await api.put(
        PRODUCTION,
        json={"version": 1, "enabled": True, "rules": RULES, "fallthrough": {"variation": "false"}},
    )
    assert r.status_code == 200, r.text

    assert (await api.post(KILL)).status_code == 200

    config = (await api.get(PRODUCTION)).json()
    assert config["enabled"] is False
    assert [rule["clauses"][0]["values"] for rule in config["rules"]] == [["EG"]]
    assert config["rules"][0]["variation"] == "true"
    assert config["fallthrough"] == {"variation": "false", "rollout": None}


async def test_kill_unknown_env_404(api: httpx.AsyncClient) -> None:
    await setup_flag(api)

    assert_error(
        await api.post("/api/projects/demo/flags/new-ui/environments/nope/kill"), 404, "not_found"
    )

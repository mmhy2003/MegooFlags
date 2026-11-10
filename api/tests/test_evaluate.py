from typing import Any

import httpx
import pytest
from helpers import assert_error, create_flag, create_project

FLAGS = "/api/projects/demo/flags"
ENVS = "/api/projects/demo/environments"


async def evaluate(api: httpx.AsyncClient, env: str = "production", **body: Any) -> httpx.Response:
    return await api.post(f"{ENVS}/{env}/evaluate", json=body)


async def setup_color(api: httpx.AsyncClient) -> None:
    await create_project(api)
    variations = [{"key": k, "value": k} for k in ("red", "blue", "green")]
    await create_flag(api, key="color", type="string", variations=variations, off_variation="red")


async def test_unknown_project_404(api: httpx.AsyncClient) -> None:
    r = await evaluate(api, context={})

    assert_error(r, 404, "not_found")


async def test_unknown_environment_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await evaluate(api, env="nope", context={})

    assert_error(r, 404, "not_found")


@pytest.mark.parametrize("body", [{"context": [1]}, {"context": "x"}, {}])
async def test_invalid_context_422(api: httpx.AsyncClient, body: dict[str, Any]) -> None:
    await create_project(api)

    r = await evaluate(api, **body)

    assert_error(r, 422, "validation_error")


async def test_all_flags_sorted_without_archived(api: httpx.AsyncClient) -> None:
    await create_project(api)
    for key in ("zeta", "alpha", "old", "mid"):
        await create_flag(api, key=key)
    r = await api.post(f"{FLAGS}/old/archive")
    assert r.status_code == 200, r.text

    r = await evaluate(api, context={"key": "u"})

    assert r.status_code == 200, r.text
    assert [res["key"] for res in r.json()["results"]] == ["alpha", "mid", "zeta"]


async def test_unknown_flag_not_found(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await evaluate(api, context={"key": "u"}, flags=["nope"])

    assert r.status_code == 200, r.text
    [result] = r.json()["results"]
    assert result["key"] == "nope"
    assert result["error_code"] == "FLAG_NOT_FOUND"
    assert result["reason"] == "ERROR"
    assert result["value"] is None
    assert result["variant"] is None


async def test_rule_match_differs_by_environment(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    r = await api.put(
        f"{FLAGS}/color/environments/production",
        json={
            "version": 1,
            "enabled": True,
            "rules": [
                {
                    "clauses": [{"attribute": "plan", "operator": "in", "values": ["pro"]}],
                    "variation": "green",
                }
            ],
            "fallthrough": {"variation": "blue"},
        },
    )
    assert r.status_code == 200, r.text
    context = {"key": "u", "plan": "pro"}

    production = await evaluate(api, context=context, flags=["color"])
    staging = await evaluate(api, env="staging", context=context, flags=["color"])

    assert production.status_code == 200, production.text
    assert production.json()["results"] == [
        {
            "key": "color",
            "value": "green",
            "variant": "green",
            "reason": "TARGETING_MATCH",
            "error_code": None,
            "error_message": None,
            "metadata": {"rule_index": 0},
        }
    ]
    assert staging.status_code == 200, staging.text
    [result] = staging.json()["results"]
    assert result["reason"] == "DISABLED"


async def test_disabled_flag_serves_off_variation(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await evaluate(api, context={"key": "u"})

    assert r.status_code == 200, r.text
    [result] = r.json()["results"]
    assert result["key"] == "color"
    assert result["reason"] == "DISABLED"
    assert result["variant"] == "red"
    assert result["value"] == "red"
    assert result["error_code"] is None

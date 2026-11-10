import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from helpers import assert_error, count_statements, create_flag, create_project, create_sdk_key
from jsonschema import Draft202012Validator
from sqlalchemy import select

from megooflags_api.etags import canonical_json, derived_etag, etag_matches
from megooflags_api.models import Environment, Project
from megooflags_api.services.ruleset import EnvRef, build_ruleset

SPEC = Path(__file__).resolve().parents[2] / "spec"
FLAGS = "/api/projects/demo/flags"
RULESET = "/sdk/v1/ruleset"
EVALUATE = "/sdk/v1/evaluate"


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def setup_demo(api: httpx.AsyncClient) -> None:
    """Flag `new-ui` is on in development only; `color` has a rule in production."""
    await create_project(api)
    await create_flag(api, key="new-ui")
    r = await api.post(f"{FLAGS}/new-ui/environments/development/toggle", json={"enabled": True})
    assert r.status_code == 200, r.text
    variations = [{"key": k, "value": k} for k in ("red", "blue")]
    await create_flag(api, key="color", type="string", variations=variations, off_variation="red")
    r = await api.put(
        f"{FLAGS}/color/environments/production",
        json={
            "version": 1,
            "enabled": True,
            "rules": [
                {
                    "clauses": [{"attribute": "plan", "operator": "in", "values": ["pro"]}],
                    "variation": "blue",
                }
            ],
            "fallthrough": {"variation": "red"},
        },
    )
    assert r.status_code == 200, r.text


async def environment(app: FastAPI, env: str = "production") -> EnvRef:
    async with app.state.sessionmaker() as session:
        row = await session.scalar(
            select(Environment)
            .join(Project, Project.id == Environment.project_id)
            .where(Project.key == "demo", Environment.key == env)
        )
        assert row is not None
        return EnvRef.of(row)


async def sdk_evaluate(
    api: httpx.AsyncClient, key: str, headers: dict[str, str] | None = None, **body: Any
) -> httpx.Response:
    return await api.post(EVALUATE, json=body, headers={**bearer(key), **(headers or {})})


def test_etag_helpers() -> None:
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
    etag = derived_etag("x", "y")
    assert etag.startswith('"') and etag.endswith('"') and len(etag) == 34
    assert etag != derived_etag("xy") and etag != derived_etag("x\0", "y")
    assert etag_matches(etag, etag)
    assert etag_matches(f'"other", W/{etag}', etag)
    assert etag_matches("*", etag)
    assert not etag_matches(None, etag)
    assert not etag_matches('"other"', etag)


async def test_ruleset_matches_builder_and_schema(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "server"))["key"]

    r = await api.get(RULESET, headers=bearer(key))

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/json"
    assert r.headers["cache-control"] == "no-cache"
    ruleset = r.json()
    async with app.state.sessionmaker() as session:
        assert ruleset == await build_ruleset(session, await environment(app))
    assert ruleset["environment"] == "production"
    assert ruleset["flags"]["new-ui"]["enabled"] is False
    schema = json.loads((SPEC / "ruleset.schema.json").read_text())
    Draft202012Validator(schema).validate(ruleset)


async def test_ruleset_etag_and_304(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    headers = bearer((await create_sdk_key(api, "server"))["key"])
    first = await api.get(RULESET, headers=headers)
    etag = first.headers["etag"]

    r = await api.get(RULESET, headers={**headers, "If-None-Match": etag})

    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag

    assert (await api.post(f"{FLAGS}/color/environments/production/kill")).status_code == 200
    app.state.ruleset_cache.invalidate((await environment(app)).project_id)
    r = await api.get(RULESET, headers={**headers, "If-None-Match": etag})

    assert r.status_code == 200, r.text
    assert r.headers["etag"] != etag
    assert r.json()["flags"]["color"]["enabled"] is False


async def test_ruleset_client_key_403(api: httpx.AsyncClient) -> None:
    await create_project(api)
    key = (await create_sdk_key(api, "client"))["key"]

    assert_error(await api.get(RULESET, headers=bearer(key)), 403, "forbidden")


async def test_evaluate_metadata_for_server_keys_only(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    server = (await create_sdk_key(api, "server"))["key"]
    client = (await create_sdk_key(api, "client"))["key"]
    context = {"key": "u", "plan": "pro"}

    r = await sdk_evaluate(api, server, context=context)
    assert r.status_code == 200, r.text
    results = {res["key"]: res for res in r.json()["results"]}
    assert set(results) == {"color", "new-ui"}
    assert results["color"]["value"] == "blue"
    assert results["color"]["reason"] == "TARGETING_MATCH"
    assert "metadata" in results["color"]

    r = await sdk_evaluate(api, client, context=context, flags=["color", "nope"])
    assert r.status_code == 200, r.text
    results = {res["key"]: res for res in r.json()["results"]}
    assert results["color"]["value"] == "blue"
    assert results["nope"]["error_code"] == "FLAG_NOT_FOUND"
    assert all("metadata" not in res for res in results.values())


async def test_evaluate_etag_per_context_and_kind(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    server = (await create_sdk_key(api, "server"))["key"]
    client = (await create_sdk_key(api, "client"))["key"]

    a = (await sdk_evaluate(api, client, context={"key": "a"})).headers["etag"]
    b = (await sdk_evaluate(api, client, context={"key": "b"})).headers["etag"]
    s = (await sdk_evaluate(api, server, context={"key": "a"})).headers["etag"]
    flags = (await sdk_evaluate(api, client, context={"key": "a"}, flags=["color"])).headers["etag"]
    assert len({a, b, s, flags}) == 4

    r = await sdk_evaluate(api, client, {"If-None-Match": a}, context={"key": "a"})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == a


async def test_warm_evaluate_issues_no_statements(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "client"))["key"]
    assert (await sdk_evaluate(api, key, context={"key": "u"})).status_code == 200

    with count_statements(app.state.engine) as statements:
        r = await sdk_evaluate(api, key, context={"key": "u"})

    assert r.status_code == 200, r.text
    assert statements == []


PREFLIGHT = {"Origin": "https://app.example", "Access-Control-Request-Method": "POST"}


async def test_cors_preflight(api: httpx.AsyncClient) -> None:
    r = await api.options(EVALUATE, headers=PREFLIGHT)

    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "*"
    assert r.headers["access-control-allow-methods"] == "GET, POST, OPTIONS"
    assert r.headers["access-control-allow-headers"] == (
        "Authorization, Content-Type, X-API-Key, If-None-Match, Last-Event-ID"
    )
    assert r.headers["access-control-max-age"] == "600"
    assert "access-control-allow-credentials" not in r.headers


async def test_cors_on_client_endpoints_only(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    client = (await create_sdk_key(api, "client"))["key"]
    server = (await create_sdk_key(api, "server"))["key"]
    origin = {"Origin": "https://app.example"}

    r = await sdk_evaluate(api, client, origin, context={"key": "u"})
    assert r.status_code == 200, r.text
    assert r.headers["access-control-allow-origin"] == "*"
    assert r.headers["access-control-expose-headers"] == "ETag, Retry-After"
    assert "access-control-allow-credentials" not in r.headers

    r = await api.post(EVALUATE, json={"context": {}}, headers=origin)
    assert_error(r, 401, "unauthorized")
    assert r.headers["access-control-allow-origin"] == "*"

    r = await api.get(RULESET, headers={**bearer(server), **origin})
    assert r.status_code == 200, r.text
    assert "access-control-allow-origin" not in r.headers
    r = await api.options(RULESET, headers=PREFLIGHT)
    assert "access-control-allow-origin" not in r.headers

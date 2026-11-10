from typing import Any

import httpx
import pytest
from conftest import ClientFactory
from fastapi import FastAPI
from helpers import assert_error, create_flag, create_project, create_sdk_key
from jsonschema import Draft202012Validator
from test_sdk_auth import wait_for_fresh_window

from megooflags_api.evaluation import Resolution
from megooflags_api.routers import ofrep
from megooflags_api.settings import Settings

FLAGS = "/api/projects/demo/flags"
SINGLE = "/ofrep/v1/evaluate/flags/{key}"
BULK = "/ofrep/v1/evaluate/flags"

# Shapes from the OFREP OpenAPI (open-feature/protocol, service/openapi.yaml).
METADATA = {
    "type": "object",
    "additionalProperties": {
        "oneOf": [{"type": "boolean"}, {"type": "string"}, {"type": "number"}]
    },
}
SUCCESS_SCHEMA = {
    "type": "object",
    "required": ["key", "value", "reason"],
    "properties": {
        "key": {"type": "string"},
        "value": {},
        "reason": {"type": "string"},
        "variant": {"type": "string"},
        "metadata": METADATA,
    },
}
ERROR_SCHEMA = {
    "type": "object",
    "required": ["key", "errorCode"],
    "properties": {
        "key": {"type": "string"},
        "errorCode": {
            "type": "string",
            "enum": [
                "FLAG_NOT_FOUND",
                "PARSE_ERROR",
                "TYPE_MISMATCH",
                "TARGETING_KEY_MISSING",
                "INVALID_CONTEXT",
                "GENERAL",
            ],
        },
        "errorDetails": {"type": "string"},
    },
}
BULK_SCHEMA = {
    "type": "object",
    "required": ["flags"],
    "properties": {"flags": {"type": "array", "items": {"oneOf": [SUCCESS_SCHEMA, ERROR_SCHEMA]}}},
}


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def validate(schema: dict[str, Any], body: Any) -> None:
    Draft202012Validator(schema).validate(body)


async def setup_demo(api: httpx.AsyncClient) -> None:
    """`color` serves blue to the user "u1"; `gated` requires `color` to be blue."""
    await create_project(api)
    variations = [{"key": k, "value": k} for k in ("red", "blue")]
    await create_flag(api, key="color", type="string", variations=variations, off_variation="red")
    r = await api.put(
        f"{FLAGS}/color/environments/production",
        json={
            "version": 1,
            "enabled": True,
            "rules": [
                {
                    "clauses": [{"attribute": "key", "operator": "in", "values": ["u1"]}],
                    "variation": "blue",
                }
            ],
            "fallthrough": {"variation": "red"},
        },
    )
    assert r.status_code == 200, r.text
    await create_flag(api, key="gated")
    url = f"{FLAGS}/gated/environments/production"
    current = (await api.get(url)).json()
    r = await api.put(
        url,
        json={
            "version": current["version"],
            "enabled": True,
            "fallthrough": current["fallthrough"],
            "prerequisites": [{"flag": "color", "variation": "blue"}],
        },
    )
    assert r.status_code == 200, r.text


async def single(
    api: httpx.AsyncClient, flag: str, headers: dict[str, str], context: Any = None
) -> httpx.Response:
    body = {} if context is None else {"context": context}
    return await api.post(SINGLE.format(key=flag), json=body, headers=headers)


async def test_single_success_with_bearer_and_x_api_key(
    api: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "server"))["key"]

    for headers in (bearer(key), {"X-API-Key": key}):
        r = await single(anon, "color", headers, {"targetingKey": "u2"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {
            "key": "color",
            "value": "red",
            "reason": "DEFAULT",
            "variant": "red",
            "metadata": {},
        }
        validate(SUCCESS_SCHEMA, body)


async def test_targeting_key_drives_targeting(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "server"))["key"]

    r = await single(api, "color", bearer(key), {"targetingKey": "u1"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["value"] == "blue"
    assert body["reason"] == "TARGETING_MATCH"
    assert body["metadata"] == {"rule_index": 0}
    validate(SUCCESS_SCHEMA, body)


async def test_unknown_flag_404(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "client"))["key"]

    r = await single(api, "nope", bearer(key))

    assert r.status_code == 404, r.text
    body = r.json()
    assert body == {
        "key": "nope",
        "errorCode": "FLAG_NOT_FOUND",
        "errorDetails": body["errorDetails"],
    }
    assert "nope" in body["errorDetails"]
    validate(ERROR_SCHEMA, body)


async def test_context_must_be_an_object(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "client"))["key"]

    r = await single(api, "color", bearer(key), "not-an-object")

    assert_error(r, 422, "validation_error")


async def test_evaluation_error_400(
    api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "client"))["key"]

    def broken(*args: Any, **kwargs: Any) -> Resolution:
        return Resolution(None, None, "ERROR", "PARSE_ERROR", "x")

    monkeypatch.setattr(ofrep, "evaluate", broken)
    r = await single(api, "color", bearer(key), {"targetingKey": "u1"})

    assert r.status_code == 400, r.text
    assert r.json() == {"key": "color", "errorCode": "PARSE_ERROR", "errorDetails": "x"}
    validate(ERROR_SCHEMA, r.json())


async def test_prerequisite_failed_metadata_for_server_keys_only(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    server = (await create_sdk_key(api, "server"))["key"]
    client = (await create_sdk_key(api, "client"))["key"]
    context = {"targetingKey": "u2"}

    r = await single(api, "gated", bearer(server), context)
    assert r.status_code == 200, r.text
    assert r.json()["reason"] == "PREREQUISITE_FAILED"
    assert r.json()["metadata"] == {"prerequisite": "color"}

    r = await single(api, "gated", bearer(client), context)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "key": "gated",
        "value": False,
        "reason": "PREREQUISITE_FAILED",
        "variant": "false",
        "metadata": {},
    }


async def test_bulk_sorted_with_etag_and_304(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "client"))["key"]
    body = {"context": {"targetingKey": "u1"}}

    r = await api.post(BULK, json=body, headers=bearer(key))

    assert r.status_code == 200, r.text
    flags = r.json()["flags"]
    assert [f["key"] for f in flags] == ["color", "gated"]
    assert flags[0] == {
        "key": "color",
        "value": "blue",
        "reason": "TARGETING_MATCH",
        "variant": "blue",
        "metadata": {},
    }
    assert flags[1]["value"] is True
    validate(BULK_SCHEMA, r.json())
    etag = r.headers["etag"]

    other = await api.post(BULK, json={"context": {"targetingKey": "u2"}}, headers=bearer(key))
    assert other.headers["etag"] != etag

    r = await api.post(BULK, json=body, headers={**bearer(key), "If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag


async def test_auth_errors(api: httpx.AsyncClient, anon: httpx.AsyncClient) -> None:
    await setup_demo(api)
    created = await create_sdk_key(api, "client")
    keys = "/api/projects/demo/environments/production/sdk-keys"
    assert (await api.delete(f"{keys}/{created['id']}")).status_code == 204

    assert_error(await anon.post(BULK, json={}), 401, "unauthorized")
    assert_error(await single(anon, "color", {}), 401, "unauthorized")
    assert_error(await anon.post(BULK, json={}, headers=bearer(created["key"])), 403, "forbidden")
    r = await single(anon, "color", {"X-API-Key": created["key"]})
    assert_error(r, 403, "forbidden")


async def test_client_rate_limit_429(
    valkey_up: None, api: httpx.AsyncClient, client_for: ClientFactory
) -> None:
    await setup_demo(api)
    headers = bearer((await create_sdk_key(api, "client"))["key"])
    await wait_for_fresh_window()

    async with client_for(Settings(client_rate_limit_per_minute=1)) as limited:
        assert (await single(limited, "color", headers)).status_code == 200
        r = await limited.post(BULK, json={}, headers=headers)

    assert_error(r, 429, "rate_limited")
    assert 1 <= int(r.headers["Retry-After"]) <= 60


async def test_cors_on_ofrep(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    key = (await create_sdk_key(api, "client"))["key"]

    r = await api.post(BULK, json={}, headers={**bearer(key), "Origin": "https://app.example"})

    assert r.status_code == 200, r.text
    assert r.headers["access-control-allow-origin"] == "*"

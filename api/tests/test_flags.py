from typing import Any

import httpx
from fastapi import FastAPI
from helpers import assert_error, create_flag, create_project
from sqlalchemy import text

STRING_VARIATIONS = [{"key": "a", "value": "A"}, {"key": "b", "value": "B"}]


async def post_flag(api: httpx.AsyncClient, **body: Any) -> httpx.Response:
    return await api.post("/api/projects/demo/flags", json={"key": "f", "name": "F", **body})


async def test_create_boolean_flag(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.post(
        "/api/projects/demo/flags", json={"key": "new-ui", "name": "New UI", "type": "boolean"}
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["key"], body["name"], body["type"]) == ("new-ui", "New UI", "boolean")
    assert (body["description"], body["tags"], body["owner"]) == ("", [], None)
    assert (body["archived"], body["archived_at"]) == (False, None)
    assert [(v["key"], v["value"]) for v in body["variations"]] == [
        ("true", True),
        ("false", False),
    ]
    assert body["off_variation"] == "false"
    assert [e["environment"] for e in body["environments"]] == [
        "development",
        "staging",
        "production",
    ]
    assert all(e["enabled"] is False and e["version"] == 1 for e in body["environments"])


async def test_boolean_with_variations_is_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await post_flag(api, type="boolean", variations=[{"key": "on", "value": True}])

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "variations"}


async def test_create_string_flag(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await post_flag(
        api,
        type="string",
        variations=[{"key": "a", "name": "Alpha", "value": "A"}, {"key": "b", "value": "B"}],
        off_variation="b",
        tags=["ui", "beta", "ui"],
        owner="team-web",
        description="Text",
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["variations"] == [
        {"key": "a", "name": "Alpha", "description": "", "value": "A"},
        {"key": "b", "name": "", "description": "", "value": "B"},
    ]
    assert body["off_variation"] == "b"
    assert (body["tags"], body["owner"], body["description"]) == (
        ["ui", "beta"],
        "team-web",
        "Text",
    )


async def test_string_without_variations_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    body = assert_error(await post_flag(api, type="string"), 422, "validation_error")
    assert body["details"] == {"path": "variations"}
    r = await post_flag(api, type="string", variations=[], off_variation="a")
    assert_error(r, 422, "validation_error")


async def test_off_variation_unknown_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await post_flag(api, type="string", variations=STRING_VARIATIONS, off_variation="z")

    body = assert_error(r, 422, "invalid_reference")
    assert body["details"] == {"path": "off_variation"}


async def test_off_variation_missing_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await post_flag(api, type="string", variations=STRING_VARIATIONS)

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "off_variation"}


async def test_duplicate_variation_key_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    variations = [{"key": "a", "value": "A"}, {"key": "a", "value": "B"}]
    r = await post_flag(api, type="string", variations=variations, off_variation="a")

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "variations[1].key"}


async def test_value_type_mismatch_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    variations = [{"key": "a", "value": 1}, {"key": "b", "value": "2"}]
    r = await post_flag(api, type="number", variations=variations, off_variation="a")

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "variations[1].value"}


async def test_json_flag_accepts_null_value(api: httpx.AsyncClient) -> None:
    await create_project(api)

    variations = [{"key": "none", "value": None}, {"key": "obj", "value": {"a": [1]}}]
    r = await post_flag(api, type="json", variations=variations, off_variation="none")

    assert r.status_code == 201, r.text
    assert [v["value"] for v in r.json()["variations"]] == [None, {"a": [1]}]


async def test_duplicate_flag_key_409(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    r = await api.post(
        "/api/projects/demo/flags", json={"key": "new-ui", "name": "Again", "type": "boolean"}
    )

    body = assert_error(r, 409, "key_conflict")
    assert body["message"] == "flag 'new-ui' already exists"


async def test_same_flag_key_in_another_project(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_project(api, key="other")
    await create_flag(api)

    await create_flag(api, project="other")


async def test_get_flag_and_404(api: httpx.AsyncClient) -> None:
    await create_project(api)
    created = await create_flag(api)

    r = await api.get("/api/projects/demo/flags/new-ui")

    assert r.status_code == 200, r.text
    assert r.json() == created
    body = assert_error(await api.get("/api/projects/demo/flags/missing"), 404, "not_found")
    assert body["message"] == "flag 'missing' not found"
    assert_error(await api.get("/api/projects/missing/flags/new-ui"), 404, "not_found")
    r = await api.post(
        "/api/projects/missing/flags", json={"key": "x", "name": "X", "type": "boolean"}
    )
    assert_error(r, 404, "not_found")


async def test_default_fallthrough_is_first_variation(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    await create_flag(
        api, key="text", type="string", variations=STRING_VARIATIONS, off_variation="b"
    )

    async with app.state.engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT f.key, v.key, fe.enabled, fe.version, off.key"
                    " FROM flag_environment fe"
                    " JOIN flag f ON f.id = fe.flag_id"
                    " JOIN variation v ON v.id = fe.fallthrough_variation_id"
                    " JOIN variation off ON off.id = f.off_variation_id"
                )
            )
        ).all()
    assert sorted(tuple(r) for r in rows) == sorted(
        [("new-ui", "true", False, 1, "false")] * 3 + [("text", "a", False, 1, "b")] * 3
    )


async def test_new_environment_backfills_flag(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    r = await api.post("/api/projects/demo/environments", json={"key": "qa", "name": "QA"})
    assert r.status_code == 201, r.text

    body = (await api.get("/api/projects/demo/flags/new-ui")).json()
    assert [e["environment"] for e in body["environments"]] == [
        "development",
        "staging",
        "production",
        "qa",
    ]

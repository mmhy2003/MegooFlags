from typing import Any

import httpx
from fastapi import FastAPI
from helpers import assert_error, count_statements, create_flag, create_project
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


async def list_keys(api: httpx.AsyncClient, **params: Any) -> list[str]:
    r = await api.get("/api/projects/demo/flags", params=params)
    assert r.status_code == 200, r.text
    return [item["key"] for item in r.json()["items"]]


async def test_list_default_hides_archived(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api, key="live")
    await create_flag(api, key="old")
    async with app.state.engine.begin() as conn:
        await conn.execute(text("UPDATE flag SET archived_at = now() WHERE key = 'old'"))

    assert await list_keys(api) == ["live"]
    assert await list_keys(api, archived="true") == ["old"]


async def test_list_filters(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api, key="checkout-v2", tags=["web", "beta"], owner="team-web")
    await create_flag(api, key="search", name="New Checkout Search", tags=["web"])
    await create_flag(
        api,
        key="price",
        type="number",
        variations=[{"key": "a", "value": 1}],
        off_variation="a",
        tags=["beta"],
        owner="team-web-2",
    )
    await create_flag(api, key="under_score", name="100% literal")

    assert await list_keys(api, q="CHECKOUT") == ["checkout-v2", "search"]
    assert await list_keys(api, q="%") == ["under_score"]
    assert await list_keys(api, q="_") == ["under_score"]
    assert await list_keys(api, q="\\") == []
    assert await list_keys(api, tag="web") == ["checkout-v2", "search"]
    assert await list_keys(api, tag=["web", "beta"]) == ["checkout-v2"]
    assert await list_keys(api, owner="team-web") == ["checkout-v2"]
    assert await list_keys(api, type="number") == ["price"]
    assert await list_keys(api, type="boolean", tag="beta") == ["checkout-v2"]


async def test_list_paginates_by_key(api: httpx.AsyncClient) -> None:
    await create_project(api)
    for key in ["e", "c", "a", "d", "b"]:
        await create_flag(api, key=key)

    keys: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        r = await api.get("/api/projects/demo/flags", params=params)
        assert r.status_code == 200, r.text
        page = r.json()
        keys += [f["key"] for f in page["items"]]
        cursor = page["next_cursor"]
        pages += 1
        if cursor is None:
            break

    assert keys == ["a", "b", "c", "d", "e"]
    assert pages == 3
    for limit in (0, 501):
        r = await api.get("/api/projects/demo/flags", params={"limit": limit})
        assert_error(r, 422, "validation_error")
    r = await api.get("/api/projects/demo/flags", params={"cursor": "!!not base64!!"})
    assert_error(r, 422, "validation_error")
    assert_error(await api.get("/api/projects/missing/flags"), 404, "not_found")


async def test_list_items_have_env_states(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    r = await api.get("/api/projects/demo/flags")

    assert r.status_code == 200, r.text
    [item] = r.json()["items"]
    assert "variations" not in item
    assert "off_variation" not in item
    assert [(e["environment"], e["enabled"], e["version"]) for e in item["environments"]] == [
        ("development", False, 1),
        ("staging", False, 1),
        ("production", False, 1),
    ]


async def toggle_on(api: httpx.AsyncClient, flag: str, env: str) -> None:
    r = await api.post(
        f"/api/projects/demo/flags/{flag}/environments/{env}/toggle", json={"enabled": True}
    )
    assert r.status_code == 200, r.text


async def test_list_filters_by_state_in_environment(api: httpx.AsyncClient) -> None:
    await create_project(api)
    for key in ["a", "b", "c"]:
        await create_flag(api, key=key)
    await toggle_on(api, "b", "staging")
    await toggle_on(api, "c", "production")

    assert await list_keys(api, env="staging", enabled="true") == ["b"]
    assert await list_keys(api, env="staging", enabled="false") == ["a", "c"]
    assert await list_keys(api, env="production", enabled="true") == ["c"]
    assert await list_keys(api, env="staging", enabled="true", q="b") == ["b"]
    assert await list_keys(api, env="staging") == ["a", "b", "c"]


async def test_list_enabled_needs_env(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.get("/api/projects/demo/flags", params={"enabled": "true"})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "enabled"}


async def test_list_unknown_environment_is_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.get("/api/projects/demo/flags", params={"env": "nope", "enabled": "true"})

    assert_error(r, 404, "not_found")


async def test_list_env_filter_query_count_is_fixed(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)

    async def add_flags_and_count(start: int, stop: int) -> int:
        for i in range(start, stop):
            await create_flag(api, key=f"f{i:02d}")
        with count_statements(app.state.engine) as statements:
            keys = await list_keys(api, env="staging", enabled="false")
        assert len(keys) == stop
        return len(statements)

    assert await add_flags_and_count(0, 3) == await add_flags_and_count(3, 33)


async def test_patch_flag(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(
        api,
        key="text",
        type="string",
        variations=STRING_VARIATIONS,
        off_variation="a",
        owner="team-web",
    )

    r = await api.patch(
        "/api/projects/demo/flags/text",
        json={
            "name": "Renamed",
            "description": "Changed",
            "tags": ["x", "y", "x"],
            "owner": None,
            "off_variation": "b",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["name"], body["description"], body["tags"]) == ("Renamed", "Changed", ["x", "y"])
    assert (body["owner"], body["off_variation"]) == (None, "b")
    assert (await api.get("/api/projects/demo/flags/text")).json() == body

    r = await api.patch("/api/projects/demo/flags/text", json={"owner": "team-api"})
    assert r.status_code == 200, r.text
    assert (r.json()["owner"], r.json()["name"], r.json()["tags"]) == (
        "team-api",
        "Renamed",
        ["x", "y"],
    )
    r = await api.patch("/api/projects/demo/flags/text", json={"name": "Again"})
    assert r.json()["owner"] == "team-api"
    assert_error(
        await api.patch("/api/projects/demo/flags/missing", json={"name": "X"}), 404, "not_found"
    )


async def test_patch_off_variation_unknown_422(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    r = await api.patch("/api/projects/demo/flags/new-ui", json={"off_variation": "maybe"})

    body = assert_error(r, 422, "invalid_reference")
    assert body["details"] == {"path": "off_variation"}


async def test_patch_type_or_key_422(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    for field, value in [("type", "string"), ("key", "renamed")]:
        r = await api.patch("/api/projects/demo/flags/new-ui", json={field: value})
        assert_error(r, 422, "validation_error")
    assert (await api.get("/api/projects/demo/flags/new-ui")).json()["type"] == "boolean"


async def test_archive_sets_archived(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    r = await api.post("/api/projects/demo/flags/new-ui/archive")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived"] is True
    assert body["archived_at"] is not None
    assert await list_keys(api) == []
    assert await list_keys(api, archived="true") == ["new-ui"]

    again = await api.post("/api/projects/demo/flags/new-ui/archive")
    assert again.status_code == 200, again.text
    assert again.json()["archived_at"] == body["archived_at"]


async def test_restore_clears_archived(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    await api.post("/api/projects/demo/flags/new-ui/archive")

    r = await api.post("/api/projects/demo/flags/new-ui/restore")

    assert r.status_code == 200, r.text
    assert (r.json()["archived"], r.json()["archived_at"]) == (False, None)
    assert await list_keys(api) == ["new-ui"]

    again = await api.post("/api/projects/demo/flags/new-ui/restore")
    assert again.status_code == 200, again.text
    assert (again.json()["archived"], again.json()["archived_at"]) == (False, None)


async def test_delete_requires_archive(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    assert_error(await api.delete("/api/projects/demo/flags/new-ui"), 409, "not_archived")
    assert (await api.get("/api/projects/demo/flags/new-ui")).status_code == 200


async def test_delete_archived_204(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    await api.post("/api/projects/demo/flags/new-ui/archive")

    r = await api.delete("/api/projects/demo/flags/new-ui")

    assert r.status_code == 204, r.text
    assert_error(await api.get("/api/projects/demo/flags/new-ui"), 404, "not_found")
    await create_flag(api)


async def test_archive_and_delete_blocked_by_prerequisite_dependent(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api, key="a")
    await create_flag(api, key="b")
    r = await api.put(
        "/api/projects/demo/flags/b/environments/production",
        json={
            "version": 1,
            "enabled": False,
            "fallthrough": {"variation": "true"},
            "prerequisites": [{"flag": "a", "variation": "true"}],
        },
    )
    assert r.status_code == 200, r.text

    body = assert_error(await api.post("/api/projects/demo/flags/a/archive"), 409, "in_use")
    assert body["message"] == "flag 'a' is a prerequisite of other flags"
    assert body["details"] == {"used_by": [{"flag": "b", "environment": "production"}]}
    assert (await api.get("/api/projects/demo/flags/a")).json()["archived"] is False

    assert (await api.post("/api/projects/demo/flags/b/archive")).status_code == 200
    assert (await api.delete("/api/projects/demo/flags/b")).status_code == 204

    assert (await api.post("/api/projects/demo/flags/a/archive")).status_code == 200
    assert (await api.delete("/api/projects/demo/flags/a")).status_code == 204

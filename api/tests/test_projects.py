import httpx
import pytest
from helpers import assert_error, create_project


async def test_create_returns_201_with_default_envs(api: httpx.AsyncClient) -> None:
    r = await api.post("/api/projects", json={"key": "demo", "name": "Demo"})

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["key"] == "demo"
    assert body["name"] == "Demo"
    assert body["description"] == ""
    assert [e["key"] for e in body["environments"]] == ["development", "staging", "production"]
    assert [e["name"] for e in body["environments"]] == ["Development", "Staging", "Production"]
    assert [e["sort_order"] for e in body["environments"]] == [0, 1, 2]


async def test_create_with_explicit_envs(api: httpx.AsyncClient) -> None:
    body = await create_project(api, environments=[{"key": "prod", "name": "Prod"}])

    assert [(e["key"], e["name"], e["sort_order"]) for e in body["environments"]] == [
        ("prod", "Prod", 0)
    ]


async def test_create_with_empty_env_list_is_422(api: httpx.AsyncClient) -> None:
    r = await api.post("/api/projects", json={"key": "demo", "name": "Demo", "environments": []})

    assert_error(r, 422, "validation_error")


async def test_create_with_duplicate_env_keys_is_422(api: httpx.AsyncClient) -> None:
    envs = [{"key": "prod", "name": "Prod"}, {"key": "prod", "name": "Prod again"}]
    r = await api.post("/api/projects", json={"key": "demo", "name": "Demo", "environments": envs})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "environments[1].key"}


async def test_duplicate_key_409(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.post("/api/projects", json={"key": "demo", "name": "Again"})

    assert_error(r, 409, "key_conflict")


@pytest.mark.parametrize("key", ["Demo", "-x", "a" * 101])
async def test_invalid_key_422(api: httpx.AsyncClient, key: str) -> None:
    r = await api.post("/api/projects", json={"key": key, "name": "Demo"})

    assert_error(r, 422, "validation_error")


async def test_get(api: httpx.AsyncClient) -> None:
    created = await create_project(api)

    r = await api.get("/api/projects/demo")

    assert r.status_code == 200
    assert r.json() == created


async def test_get_404(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/projects/missing")

    body = assert_error(r, 404, "not_found")
    assert body["message"] == "project 'missing' not found"


async def test_list_paginates(api: httpx.AsyncClient) -> None:
    for key in ["e", "c", "a", "d", "b"]:
        await create_project(api, key=key)

    keys: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        r = await api.get("/api/projects", params=params)
        assert r.status_code == 200, r.text
        page = r.json()
        assert len(page["items"]) <= 2
        assert "environments" not in page["items"][0]
        keys += [p["key"] for p in page["items"]]
        cursor = page["next_cursor"]
        pages += 1
        if cursor is None:
            break

    assert keys == ["a", "b", "c", "d", "e"]
    assert pages == 3


async def test_list_bad_cursor_is_422(api: httpx.AsyncClient) -> None:
    r = await api.get("/api/projects", params={"cursor": "!!not base64!!"})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "cursor"}


async def test_patch_updates_name_and_description(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.patch(
        "/api/projects/demo", json={"name": "Renamed", "description": "The demo project"}
    )

    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed"
    assert r.json()["description"] == "The demo project"
    assert len(r.json()["environments"]) == 3
    assert (await api.get("/api/projects/demo")).json()["name"] == "Renamed"


async def test_stale_after_days_defaults_to_30(api: httpx.AsyncClient) -> None:
    created = await create_project(api)

    assert created["stale_after_days"] == 30
    listed = (await api.get("/api/projects")).json()["items"]
    assert [p["stale_after_days"] for p in listed] == [30]


async def test_patch_stale_after_days(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.patch("/api/projects/demo", json={"stale_after_days": 7})

    assert r.status_code == 200, r.text
    assert r.json()["stale_after_days"] == 7
    assert (await api.get("/api/projects/demo")).json()["stale_after_days"] == 7


@pytest.mark.parametrize("days", [0, 366])
async def test_patch_stale_after_days_out_of_range_is_422(
    api: httpx.AsyncClient, days: int
) -> None:
    await create_project(api)

    r = await api.patch("/api/projects/demo", json={"stale_after_days": days})

    assert_error(r, 422, "validation_error")


async def test_patch_key_is_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.patch("/api/projects/demo", json={"key": "other"})

    assert_error(r, 422, "validation_error")


async def test_patch_404(api: httpx.AsyncClient) -> None:
    r = await api.patch("/api/projects/missing", json={"name": "X"})

    assert_error(r, 404, "not_found")


async def test_delete_204_then_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.delete("/api/projects/demo")

    assert r.status_code == 204
    assert_error(await api.get("/api/projects/demo"), 404, "not_found")
    assert_error(await api.delete("/api/projects/demo"), 404, "not_found")

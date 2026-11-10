from datetime import datetime
from typing import Any

import httpx
import pytest
from helpers import assert_error, create_flag, create_project

COLOR = "/api/projects/demo/flags/color"


async def setup_color(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(
        api,
        key="color",
        type="string",
        variations=[{"key": "a", "value": "red"}, {"key": "b", "value": "green"}],
        off_variation="b",
    )


async def test_add_variation_to_string_flag(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.post(f"{COLOR}/variations", json={"key": "c", "name": "Blue", "value": "blue"})

    assert r.status_code == 201, r.text
    assert r.json() == {"key": "c", "name": "Blue", "description": "", "value": "blue"}
    flag = (await api.get(COLOR)).json()
    assert [v["key"] for v in flag["variations"]] == ["a", "b", "c"]


async def test_add_wrong_type_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.post(f"{COLOR}/variations", json={"key": "c", "value": 3})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "value"}


async def test_duplicate_key_409(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.post(f"{COLOR}/variations", json={"key": "a", "value": "x"})

    assert_error(r, 409, "key_conflict")


async def test_boolean_add_or_delete_422(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api, key="new-ui")
    base = "/api/projects/demo/flags/new-ui/variations"

    added = assert_error(
        await api.post(base, json={"key": "maybe", "value": True}), 422, "validation_error"
    )
    deleted = assert_error(await api.delete(f"{base}/true"), 422, "validation_error")

    assert added["message"] == deleted["message"] == "boolean flags have fixed variations"


async def test_patch_variation(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.patch(
        f"{COLOR}/variations/a", json={"name": "Red", "description": "Warm", "value": "crimson"}
    )

    assert r.status_code == 200, r.text
    assert r.json() == {"key": "a", "name": "Red", "description": "Warm", "value": "crimson"}
    flag = (await api.get(COLOR)).json()
    assert flag["variations"][0]["value"] == "crimson"


async def test_patch_variation_partial(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.patch(f"{COLOR}/variations/a", json={"name": "Red"})

    assert r.status_code == 200, r.text
    assert r.json() == {"key": "a", "name": "Red", "description": "", "value": "red"}


async def test_patch_variation_wrong_type_422(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    r = await api.patch(f"{COLOR}/variations/a", json={"value": None})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "value"}


async def test_patch_json_variation_to_null(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(
        api, key="cfg", type="json", variations=[{"key": "a", "value": {"x": 1}}], off_variation="a"
    )

    r = await api.patch("/api/projects/demo/flags/cfg/variations/a", json={"value": None})

    assert r.status_code == 200, r.text
    assert r.json()["value"] is None


async def test_patch_boolean_variation(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api, key="new-ui")
    url = "/api/projects/demo/flags/new-ui/variations/true"

    renamed = await api.patch(url, json={"name": "On"})
    changed = await api.patch(url, json={"value": False})

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "On"
    assert_error(changed, 422, "validation_error")


async def test_delete_unused_204(api: httpx.AsyncClient) -> None:
    await setup_color(api)
    await api.post(f"{COLOR}/variations", json={"key": "c", "value": "blue"})

    r = await api.delete(f"{COLOR}/variations/c")

    assert r.status_code == 204, r.text
    flag = (await api.get(COLOR)).json()
    assert [v["key"] for v in flag["variations"]] == ["a", "b"]


async def test_delete_off_variation_409(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    body = assert_error(await api.delete(f"{COLOR}/variations/b"), 409, "in_use")

    assert {"usage": "off_variation"} in body["details"]["used_by"]


async def test_delete_fallthrough_variation_409(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    body = assert_error(await api.delete(f"{COLOR}/variations/a"), 409, "in_use")

    used_by = body["details"]["used_by"]
    assert {"usage": "fallthrough", "flag": "color", "environment": "development"} in used_by
    assert len(used_by) == 3  # one per environment


async def test_unknown_variation_404(api: httpx.AsyncClient) -> None:
    await setup_color(api)

    patched = await api.patch(f"{COLOR}/variations/zzz", json={"name": "Z"})
    deleted = await api.delete(f"{COLOR}/variations/zzz")

    assert_error(patched, 404, "not_found")
    assert_error(deleted, 404, "not_found")


async def test_unknown_flag_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.post("/api/projects/demo/flags/nope/variations", json={"key": "c", "value": 1})

    assert_error(r, 404, "not_found")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/variations", {"key": "d", "value": "yellow"}),
        ("PATCH", "/variations/b", {"value": "lime"}),
        ("DELETE", "/variations/c", None),
    ],
)
async def test_variation_writes_bump_flag_updated_at(
    api: httpx.AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    await create_project(api)
    await create_flag(
        api,
        key="color",
        type="string",
        variations=[
            {"key": "a", "value": "red"},
            {"key": "b", "value": "green"},
            {"key": "c", "value": "blue"},
        ],
        off_variation="a",
    )
    before = datetime.fromisoformat((await api.get(COLOR)).json()["updated_at"])

    r = await api.request(method, f"{COLOR}{path}", json=body)

    assert r.is_success, r.text
    after = datetime.fromisoformat((await api.get(COLOR)).json()["updated_at"])
    assert after > before

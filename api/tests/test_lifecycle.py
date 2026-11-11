from typing import Any

import httpx
from fastapi import FastAPI
from helpers import (
    assert_error,
    audit_entries,
    bearer,
    create_flag,
    create_project,
    seed_service_token,
)
from sqlalchemy import text

F = "/api/projects/demo/flags/new-ui"


async def set_lifecycle(api: httpx.AsyncClient, permanent: bool) -> httpx.Response:
    return await api.put(f"{F}/lifecycle", json={"permanent": permanent})


async def keys(api: httpx.AsyncClient, **params: Any) -> list[str]:
    r = await api.get("/api/projects/demo/flags", params=params)
    assert r.status_code == 200, r.text
    return [item["key"] for item in r.json()["items"]]


async def test_a_new_flag_is_new_and_never_evaluated(api: httpx.AsyncClient) -> None:
    await create_project(api)
    created = await create_flag(api)

    assert (created["lifecycle"], created["last_evaluated_at"]) == ("new", None)
    body = (await api.get(F)).json()
    assert (body["lifecycle"], body["last_evaluated_at"]) == ("new", None)
    [item] = (await api.get("/api/projects/demo/flags")).json()["items"]
    assert (item["lifecycle"], item["last_evaluated_at"]) == ("new", None)


async def test_archive_shows_archived_and_restore_shows_new(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    assert (await api.post(f"{F}/archive")).json()["lifecycle"] == "archived"
    assert (await api.post(f"{F}/restore")).json()["lifecycle"] == "new"


async def test_mark_and_clear_permanent(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    r = await set_lifecycle(api, True)
    assert r.status_code == 200, r.text
    assert (r.json()["key"], r.json()["lifecycle"]) == ("new-ui", "permanent")
    assert (await api.get(F)).json()["lifecycle"] == "permanent"

    r = await set_lifecycle(api, False)
    assert r.status_code == 200, r.text
    assert r.json()["lifecycle"] == "new"

    entries = await audit_entries(app, "flag.lifecycle.update")
    assert [
        (e["resource_key"], e["before"]["lifecycle"], e["after"]["lifecycle"]) for e in entries
    ] == [
        ("new-ui", "new", "permanent"),
        ("new-ui", "permanent", "new"),
    ]


async def test_clearing_permanent_on_an_evaluated_flag_gives_active(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api)
    await set_lifecycle(api, True)
    async with app.state.engine.begin() as conn:
        await conn.execute(text("UPDATE flag SET last_evaluated_at = now() WHERE key = 'new-ui'"))

    r = await set_lifecycle(api, False)

    assert r.status_code == 200, r.text
    assert r.json()["lifecycle"] == "active"
    assert r.json()["last_evaluated_at"] is not None


async def test_a_project_viewer_cannot_set_lifecycle(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    async with app.state.engine.begin() as conn:
        demo_id = await conn.scalar(text("SELECT id FROM project WHERE key = 'demo'"))
    token = await seed_service_token(app, "viewer", scope="project", project_id=demo_id)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=bearer(token)
    ) as viewer:
        assert_error(await set_lifecycle(viewer, True), 403, "forbidden")

    assert (await api.get(F)).json()["lifecycle"] == "new"


async def test_list_filters_by_lifecycle(api: httpx.AsyncClient) -> None:
    await create_project(api)
    for key in ("fresh", "kept", "gone"):
        await create_flag(api, key=key)
    assert (
        await api.put("/api/projects/demo/flags/kept/lifecycle", json={"permanent": True})
    ).status_code == 200
    assert (await api.post("/api/projects/demo/flags/gone/archive")).status_code == 200

    assert await keys(api, lifecycle="permanent") == ["kept"]
    assert await keys(api, lifecycle="new") == ["fresh"]
    assert await keys(api, lifecycle="archived") == ["gone"]
    # lifecycle wins over archived
    assert await keys(api, lifecycle="archived", archived="false") == ["gone"]
    assert await keys(api, lifecycle="new", archived="true") == ["fresh"]
    assert await keys(api, lifecycle="stale") == []


async def test_unknown_lifecycle_filter_is_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.get("/api/projects/demo/flags", params={"lifecycle": "zombie"})

    assert_error(r, 422, "validation_error")

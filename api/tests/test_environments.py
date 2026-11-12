import uuid

import httpx
import pytest
from fastapi import FastAPI
from helpers import assert_error, audit_entries, create_project
from sqlalchemy import text


async def test_list_ordered_by_sort_order(api: httpx.AsyncClient) -> None:
    await create_project(
        api,
        environments=[
            {"key": "prod", "name": "Prod", "sort_order": 2},
            {"key": "b-dev", "name": "Dev B", "sort_order": 0},
            {"key": "a-dev", "name": "Dev A", "sort_order": 0},
        ],
    )

    r = await api.get("/api/projects/demo/environments")

    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
    assert [e["key"] for e in r.json()] == ["a-dev", "b-dev", "prod"]


async def test_create_201_appends_sort_order(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.post(
        "/api/projects/demo/environments",
        json={"key": "qa", "name": "QA", "description": "Testing"},
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["key"], body["name"], body["description"], body["sort_order"]) == (
        "qa",
        "QA",
        "Testing",
        3,
    )
    listed = (await api.get("/api/projects/demo/environments")).json()
    assert [e["key"] for e in listed] == ["development", "staging", "production", "qa"]


async def test_create_with_explicit_sort_order(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.post(
        "/api/projects/demo/environments", json={"key": "qa", "name": "QA", "sort_order": 10}
    )

    assert r.status_code == 201, r.text
    assert r.json()["sort_order"] == 10


async def test_duplicate_409_key_conflict(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.post("/api/projects/demo/environments", json={"key": "staging", "name": "S"})

    body = assert_error(r, 409, "key_conflict")
    assert body["message"] == "environment 'staging' already exists"


async def test_unknown_project_404(api: httpx.AsyncClient) -> None:
    assert_error(await api.get("/api/projects/missing/environments"), 404, "not_found")
    r = await api.post("/api/projects/missing/environments", json={"key": "qa", "name": "QA"})
    body = assert_error(r, 404, "not_found")
    assert body["message"] == "project 'missing' not found"


async def test_get_and_patch(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.get("/api/projects/demo/environments/staging")
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Staging"

    r = await api.patch(
        "/api/projects/demo/environments/staging",
        json={"name": "Stage", "description": "Pre-prod", "sort_order": 7},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["key"], body["name"], body["description"], body["sort_order"]) == (
        "staging",
        "Stage",
        "Pre-prod",
        7,
    )
    assert (await api.get("/api/projects/demo/environments/staging")).json() == body

    r = await api.patch("/api/projects/demo/environments/staging", json={"key": "other"})
    assert_error(r, 422, "validation_error")


async def test_get_unknown_environment_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.get("/api/projects/demo/environments/missing")

    body = assert_error(r, 404, "not_found")
    assert body["message"] == "environment 'missing' not found"
    assert_error(
        await api.patch("/api/projects/demo/environments/missing", json={"name": "X"}),
        404,
        "not_found",
    )


async def test_delete_204_then_get_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.delete("/api/projects/demo/environments/staging")

    assert r.status_code == 204
    assert_error(await api.get("/api/projects/demo/environments/staging"), 404, "not_found")
    assert_error(await api.delete("/api/projects/demo/environments/staging"), 404, "not_found")
    listed = (await api.get("/api/projects/demo/environments")).json()
    assert [e["key"] for e in listed] == ["development", "production"]


async def test_backfill_existing_flags(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    first_variations: dict[uuid.UUID, uuid.UUID] = {}
    async with app.state.engine.begin() as conn:
        project_id = await conn.scalar(text("SELECT id FROM project WHERE key = 'demo'"))
        for i in range(3):
            flag_id = await conn.scalar(
                text(
                    "INSERT INTO flag (project_id, key, name, type)"
                    " VALUES (:p, :k, :k, 'boolean') RETURNING id"
                ),
                {"p": project_id, "k": f"flag-{i}"},
            )
            # Insert sort_order 1 first so the backfill can't rely on insertion order.
            await conn.execute(
                text(
                    "INSERT INTO variation (flag_id, key, value, sort_order)"
                    " VALUES (:f, 'false', 'false', 1)"
                ),
                {"f": flag_id},
            )
            first_variations[flag_id] = await conn.scalar(
                text(
                    "INSERT INTO variation (flag_id, key, value, sort_order)"
                    " VALUES (:f, 'true', 'true', 0) RETURNING id"
                ),
                {"f": flag_id},
            )

    r = await api.post("/api/projects/demo/environments", json={"key": "qa", "name": "QA"})
    assert r.status_code == 201, r.text

    async with app.state.engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT fe.flag_id, fe.enabled, fe.version, fe.fallthrough_variation_id"
                    " FROM flag_environment fe JOIN environment e ON e.id = fe.environment_id"
                    " WHERE e.key = 'qa'"
                )
            )
        ).all()
    assert len(rows) == 3
    for flag_id, enabled, version, fallthrough in rows:
        assert enabled is False
        assert version == 1
        assert fallthrough == first_variations[flag_id]


async def test_approvals_required_defaults_to_0(api: httpx.AsyncClient) -> None:
    await create_project(api)

    listed = (await api.get("/api/projects/demo/environments")).json()

    assert [e["approvals_required"] for e in listed] == [0, 0, 0]


async def test_patch_approvals_required(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.patch(
        "/api/projects/demo/environments/production", json={"approvals_required": 2}
    )

    assert r.status_code == 200, r.text
    assert r.json()["approvals_required"] == 2
    got = (await api.get("/api/projects/demo/environments/production")).json()
    assert got["approvals_required"] == 2
    listed = (await api.get("/api/projects/demo/environments")).json()
    assert [e["approvals_required"] for e in listed] == [0, 0, 2]
    [entry] = await audit_entries(app, "environment.update")
    assert (entry["before"]["approvals_required"], entry["after"]["approvals_required"]) == (0, 2)


@pytest.mark.parametrize("value", [11, -1])
async def test_patch_approvals_required_out_of_range_is_422(
    api: httpx.AsyncClient, value: int
) -> None:
    await create_project(api)

    r = await api.patch(
        "/api/projects/demo/environments/production", json={"approvals_required": value}
    )

    assert_error(r, 422, "validation_error")

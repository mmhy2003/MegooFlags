"""`GET /audit` and `/audit/export`: ordering, paging, filters, scoping, export and redaction."""

import csv
import io
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    assert_error,
    audit_entries,
    bearer,
    create_flag,
    create_project,
    seed_service_token,
)
from sqlalchemy import insert

from megooflags_api.models import AuditEntry
from megooflags_api.services.audit import EXPORT_BATCH

CSV_HEADER = (
    "id,occurred_at,actor_kind,actor_id,actor_label,auth_method,ip,user_agent,request_id,"
    "action,resource_type,resource_key,project,environment,before,after"
)


@pytest.fixture
async def token_client(app: FastAPI) -> AsyncIterator[Any]:
    """Makes clients that send `token` as a bearer."""
    clients: list[httpx.AsyncClient] = []

    def make(token: str, **headers: str) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={**bearer(token), **headers},
        )
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


async def project_id(api: httpx.AsyncClient, app: FastAPI, key: str) -> Any:
    [entry] = [e for e in await audit_entries(app, "project.create") if e["resource_key"] == key]
    return entry["project_id"]


async def toggle(api: httpx.AsyncClient, env: str, enabled: bool = True) -> None:
    r = await api.post(
        f"/api/projects/demo/flags/new-ui/environments/{env}/toggle", json={"enabled": enabled}
    )
    assert r.status_code == 200, r.text


async def list_all(client: httpx.AsyncClient, **params: Any) -> list[dict[str, Any]]:
    r = await client.get("/api/audit", params={"limit": 500, **params})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next_cursor"] is None
    return body["items"]  # type: ignore[no-any-return]


async def actions(client: httpx.AsyncClient, **params: Any) -> list[str]:
    return [item["action"] for item in await list_all(client, **params)]


# Ordering and paging


async def test_entries_come_newest_first_with_their_details(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    await toggle(api, "production")
    items = await list_all(api)
    assert [i["action"] for i in items[:3]] == ["flag.toggle", "flag.create", "project.create"]
    ids = [i["id"] for i in items]
    assert ids == sorted(ids, reverse=True)
    newest = items[0]
    assert newest["actor"]["kind"] == "service_token"
    assert newest["actor"]["label"] == "tests"
    assert newest["actor"]["auth_method"] == "token"
    assert newest["actor"]["id"] is not None
    assert (newest["resource_type"], newest["resource_key"]) == ("flag", "new-ui")
    assert (newest["project"], newest["environment"]) == ("demo", "production")
    assert newest["before"]["enabled"] is False
    assert newest["after"]["enabled"] is True
    assert newest["occurred_at"]


async def test_following_the_cursor_returns_the_rest_without_overlap(
    api: httpx.AsyncClient,
) -> None:
    await create_project(api)
    await create_flag(api)
    for enabled in (True, False, True):
        await toggle(api, "production", enabled)
    everything = [i["id"] for i in await list_all(api)]
    assert len(everything) >= 5

    seen: list[int] = []
    params: dict[str, Any] = {"limit": 2}
    while True:
        r = await api.get("/api/audit", params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["items"]) <= 2
        seen += [i["id"] for i in body["items"]]
        if body["next_cursor"] is None:
            break
        params["cursor"] = body["next_cursor"]
    assert seen == everything


@pytest.mark.parametrize("cursor", ["!!!", "bm90LWFuLWlk"])  # "not-an-id"
async def test_a_bad_cursor_is_422(api: httpx.AsyncClient, cursor: str) -> None:
    assert_error(await api.get("/api/audit", params={"cursor": cursor}), 422, "validation_error")


# Filters


@pytest.fixture
async def history(app: FastAPI, api: httpx.AsyncClient, token_client: Any) -> dict[str, Any]:
    """demo (with a flag toggled in staging and production), a team, and a project `other`
    created by a second token named "second"."""
    await create_project(api)
    await create_flag(api)
    await toggle(api, "staging")
    await toggle(api, "production")
    r = await api.post("/api/teams", json={"key": "ops", "name": "Ops"})
    assert r.status_code == 201, r.text
    second = token_client(await seed_service_token(app, name="second"))
    await create_project(second, "other")
    return {"second": second}


async def test_filter_by_action(api: httpx.AsyncClient, history: dict[str, Any]) -> None:
    assert await actions(api, action="flag.toggle") == ["flag.toggle", "flag.toggle"]


async def test_filter_by_resource_type(api: httpx.AsyncClient, history: dict[str, Any]) -> None:
    assert await actions(api, resource_type="team") == ["team.create"]


async def test_filter_by_project(api: httpx.AsyncClient, history: dict[str, Any]) -> None:
    items = await list_all(api, project="demo")
    assert [i["action"] for i in items] == [
        "flag.toggle",
        "flag.toggle",
        "flag.create",
        "project.create",
    ]
    assert {i["project"] for i in items} == {"demo"}


async def test_filter_by_project_and_environment(
    api: httpx.AsyncClient, history: dict[str, Any]
) -> None:
    items = await list_all(api, project="demo", env="staging")
    assert [(i["action"], i["environment"]) for i in items] == [("flag.toggle", "staging")]


async def test_env_without_project_is_422(api: httpx.AsyncClient) -> None:
    assert_error(await api.get("/api/audit", params={"env": "staging"}), 422, "validation_error")


async def test_unknown_project_or_environment_is_404(api: httpx.AsyncClient) -> None:
    await create_project(api)
    assert_error(await api.get("/api/audit", params={"project": "nope"}), 404, "not_found")
    assert_error(
        await api.get("/api/audit", params={"project": "demo", "env": "nope"}), 404, "not_found"
    )
    assert_error(await api.get("/api/audit/export", params={"project": "nope"}), 404, "not_found")


async def test_filter_by_actor_id_or_exact_label(
    app: FastAPI, api: httpx.AsyncClient, history: dict[str, Any]
) -> None:
    other_id = await project_id(api, app, "other")
    [other] = [e for e in await audit_entries(app) if e["project_id"] == other_id]
    by_id = await list_all(api, actor=str(other["actor_id"]))
    assert [(i["action"], i["resource_key"]) for i in by_id] == [("project.create", "other")]
    by_label = await list_all(api, actor="second")
    assert [i["id"] for i in by_label] == [i["id"] for i in by_id]
    assert await list_all(api, actor="secon") == []
    assert len(await list_all(api, actor="tests")) == 5


async def test_filter_by_from_and_to(api: httpx.AsyncClient, history: dict[str, Any]) -> None:
    items = await list_all(api)
    middle = items[2]
    newer = await list_all(api, **{"from": middle["occurred_at"]})
    assert [i["id"] for i in newer] == [i["id"] for i in items[:3]]
    older = await list_all(api, to=middle["occurred_at"])
    assert [i["id"] for i in older] == [i["id"] for i in items[3:]]
    between = await list_all(api, **{"from": items[4]["occurred_at"], "to": middle["occurred_at"]})
    assert [i["id"] for i in between] == [i["id"] for i in items[3:5]]


# Scoping


@pytest.fixture
async def demo_viewer(
    app: FastAPI, api: httpx.AsyncClient, history: dict[str, Any], token_client: Any
) -> httpx.AsyncClient:
    demo_id = await project_id(api, app, "demo")
    token = await seed_service_token(
        app, "viewer", scope="project", project_id=demo_id, name="viewer"
    )
    return token_client(token)  # type: ignore[no-any-return]


async def test_a_project_viewer_sees_only_that_projects_entries(
    demo_viewer: httpx.AsyncClient,
) -> None:
    items = await list_all(demo_viewer)
    assert [i["action"] for i in items] == [
        "flag.toggle",
        "flag.toggle",
        "flag.create",
        "project.create",
    ]
    assert {i["project"] for i in items} == {"demo"}


async def test_a_project_viewer_gets_404_for_another_project(
    demo_viewer: httpx.AsyncClient,
) -> None:
    for path in ("/api/audit", "/api/audit/export"):
        assert_error(await demo_viewer.get(path, params={"project": "other"}), 404, "not_found")


async def test_a_token_without_audit_read_is_forbidden(app: FastAPI, token_client: Any) -> None:
    editor = token_client(await seed_service_token(app, "editor", name="editor"))
    for path in ("/api/audit", "/api/audit/export"):
        assert_error(await editor.get(path), 403, "forbidden")


async def test_audit_needs_credentials(anon: httpx.AsyncClient) -> None:
    assert (await anon.get("/api/audit")).status_code == 401
    assert (await anon.get("/api/audit/export")).status_code == 401


async def test_entries_of_a_deleted_project_show_with_a_null_project(
    api: httpx.AsyncClient,
) -> None:
    await create_project(api, "gone")
    assert (await api.delete("/api/projects/gone")).status_code == 204
    items = await list_all(api)
    assert [(i["action"], i["resource_key"], i["project"]) for i in items] == [
        ("project.delete", "gone", None),
        ("project.create", "gone", None),
    ]


# Export


async def test_ndjson_export_has_one_entry_per_line(
    api: httpx.AsyncClient, history: dict[str, Any]
) -> None:
    r = await api.get("/api/audit/export", params={"format": "ndjson"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/x-ndjson"
    assert re.match(r'^attachment; filename="audit-.+\.ndjson"$', r.headers["content-disposition"])
    lines = r.text.splitlines()
    assert r.text.endswith("\n")
    exported = [json.loads(line) for line in lines]
    assert exported == await list_all(api)


async def test_csv_export_has_the_header_and_json_snapshots(
    api: httpx.AsyncClient, history: dict[str, Any]
) -> None:
    r = await api.get("/api/audit/export", params={"format": "csv", "action": "flag.toggle"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert re.match(r'^attachment; filename="audit-.+\.csv"$', r.headers["content-disposition"])
    assert r.text.splitlines()[0] == CSV_HEADER
    rows = list(csv.DictReader(io.StringIO(r.text)))
    listed = await list_all(api, action="flag.toggle")
    assert [int(row["id"]) for row in rows] == [i["id"] for i in listed]
    first = rows[0]
    assert (first["actor_kind"], first["actor_label"], first["auth_method"]) == (
        "service_token",
        "tests",
        "token",
    )
    assert (first["project"], first["environment"]) == ("demo", "production")
    assert json.loads(first["before"]) == listed[0]["before"]
    assert json.loads(first["after"]) == listed[0]["after"]


async def test_csv_export_defuses_formula_cells(app: FastAPI, token_client: Any) -> None:
    token = await seed_service_token(app, name="=SUM(1)")
    client = token_client(token, **{"User-Agent": "\t=cmd", "X-Request-ID": "+1"})
    r = await client.post("/api/projects", json={"key": "formula", "name": "=SUM(1)"})
    assert r.status_code == 201, r.text

    r = await client.get("/api/audit/export", params={"format": "csv"})
    assert r.status_code == 200, r.text
    rows = list(csv.reader(io.StringIO(r.text)))[1:]
    assert rows
    for row in rows:
        assert not [cell for cell in row if cell[:1] in {"=", "+", "-", "@", "\t", "\r"}], row
        assert row[4] == "'=SUM(1)"  # actor_label
        assert row[7] == "'\t=cmd"  # user_agent
        assert row[8] == "'+1"  # request_id
    created = json.loads(rows[-1][15])
    assert created["name"] == "=SUM(1)"


async def test_export_applies_filters_and_scoping(
    api: httpx.AsyncClient, demo_viewer: httpx.AsyncClient
) -> None:
    r = await demo_viewer.get("/api/audit/export", params={"format": "ndjson"})
    assert r.status_code == 200, r.text
    exported = [json.loads(line) for line in r.text.splitlines()]
    assert exported == await list_all(demo_viewer)
    assert {e["project"] for e in exported} == {"demo"}

    r = await api.get(
        "/api/audit/export", params={"format": "ndjson", "project": "demo", "env": "staging"}
    )
    assert [json.loads(line)["action"] for line in r.text.splitlines()] == ["flag.toggle"]


async def test_export_spans_several_batches(app: FastAPI, api: httpx.AsyncClient) -> None:
    rows = [
        {"actor_kind": "anonymous", "actor_label": "anonymous", "action": "x.test"}
        | {"resource_type": "x", "resource_key": f"k{n}"}
        for n in range(EXPORT_BATCH + 5)
    ]
    async with app.state.sessionmaker() as session:
        await session.execute(insert(AuditEntry), rows)
        await session.commit()
    r = await api.get("/api/audit/export", params={"format": "ndjson"})
    keys = [json.loads(line)["resource_key"] for line in r.text.splitlines()]
    assert keys == [f"k{n}" for n in reversed(range(EXPORT_BATCH + 5))]


async def test_an_unknown_format_is_422(api: httpx.AsyncClient) -> None:
    assert_error(
        await api.get("/api/audit/export", params={"format": "xml"}), 422, "validation_error"
    )


# Redaction


async def test_an_invitation_password_never_appears_in_the_log(
    api: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    password = "a very secret invitation password 42"
    r = await api.post("/api/users", json={"email": "new@example.com", "name": "New"})
    assert r.status_code == 201, r.text
    token = r.json()["invite"]["url"].rsplit("/", 1)[1]
    r = await anon.post(f"/api/invitations/{token}", json={"password": password})
    assert r.status_code == 204, r.text

    assert "invitation.accept" in await actions(api)
    for params in ({}, {"format": "csv"}, {"format": "ndjson"}):
        path = "/api/audit" if not params else "/api/audit/export"
        r = await api.get(path, params=params)
        assert r.status_code == 200, r.text
        assert password not in r.text
        assert token not in r.text

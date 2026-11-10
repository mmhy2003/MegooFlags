import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    assert_error,
    audit_entries,
    bearer,
    create_project,
    seed_binding,
    seed_service_token,
    seed_user,
)
from sqlalchemy import select

from megooflags_api.auth.principal import Principal, PrincipalKind
from megooflags_api.auth.rbac import Permission, load_authorizer
from megooflags_api.models import Project, RoleBinding
from megooflags_api.models.enums import SubjectKind
from megooflags_api.services.users import create_user


@asynccontextmanager
async def client_with(app: FastAPI, token: str) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=bearer(token)
    ) as client:
        yield client


@pytest.fixture
async def admin(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with client_with(app, await seed_service_token(app)) as client:
        yield client


async def create_team(
    client: httpx.AsyncClient, key: str = "platform", name: str = "Platform"
) -> dict[str, Any]:
    r = await client.post("/api/teams", json={"key": key, "name": name})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def add_member(client: httpx.AsyncClient, team: str, user_id: uuid.UUID) -> dict[str, Any]:
    r = await client.post(f"/api/teams/{team}/members", json={"user_id": str(user_id)})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def team_bindings(app: FastAPI, team_id: str) -> list[RoleBinding]:
    async with app.state.sessionmaker() as session:
        rows = await session.scalars(
            select(RoleBinding).where(
                RoleBinding.subject_kind == SubjectKind.TEAM,
                RoleBinding.subject_id == uuid.UUID(team_id),
            )
        )
        return list(rows)


# Teams


async def test_create_list_read_update_delete(app: FastAPI, admin: httpx.AsyncClient) -> None:
    team = await create_team(admin)
    assert (team["key"], team["name"], team["member_count"]) == ("platform", "Platform", 0)
    assert team["id"] and team["created_at"]
    await create_team(admin, "data", "Data")
    user_id = await seed_user(app, "dev@example.com", role="viewer")
    await add_member(admin, "platform", user_id)

    r = await admin.get("/api/teams")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(t["key"], t["member_count"]) for t in body["items"]] == [
        ("data", 0),
        ("platform", 1),
    ]
    assert body["next_cursor"] is None

    r = await admin.get("/api/teams/platform")
    assert r.status_code == 200, r.text
    assert r.json() == {**team, "member_count": 1}

    r = await admin.patch("/api/teams/platform", json={"name": "Platform team"})
    assert r.status_code == 200, r.text
    assert r.json() == {**team, "name": "Platform team", "member_count": 1}

    await seed_binding(app, SubjectKind.TEAM, uuid.UUID(team["id"]), "viewer")
    assert len(await team_bindings(app, team["id"])) == 1
    assert (await admin.delete("/api/teams/platform")).status_code == 204
    assert_error(await admin.get("/api/teams/platform"), 404, "not_found")
    assert await team_bindings(app, team["id"]) == []


async def test_list_pages(admin: httpx.AsyncClient) -> None:
    for key in ("a", "b", "c"):
        await create_team(admin, key, key.upper())
    r = await admin.get("/api/teams", params={"limit": 2})
    first = r.json()
    assert [t["key"] for t in first["items"]] == ["a", "b"]
    r = await admin.get("/api/teams", params={"limit": 2, "cursor": first["next_cursor"]})
    assert [t["key"] for t in r.json()["items"]] == ["c"]
    assert r.json()["next_cursor"] is None


async def test_duplicate_team_key_is_409(admin: httpx.AsyncClient) -> None:
    await create_team(admin)
    r = await admin.post("/api/teams", json={"key": "platform", "name": "Again"})
    assert_error(r, 409, "key_conflict")


async def test_unknown_team_is_404(admin: httpx.AsyncClient) -> None:
    assert_error(await admin.get("/api/teams/nope"), 404, "not_found")
    assert_error(await admin.patch("/api/teams/nope", json={"name": "x"}), 404, "not_found")
    assert_error(await admin.delete("/api/teams/nope"), 404, "not_found")
    assert_error(await admin.get("/api/teams/nope/members"), 404, "not_found")


# Members


async def test_add_list_and_remove_members(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_team(admin)
    dev = await seed_user(app, "dev@example.com", role="viewer", name="Dev")
    ops = await seed_user(app, "ops@example.com", role="viewer", name="Ops")

    member = await add_member(admin, "platform", dev)
    assert member["source"] == "manual"
    assert (member["user"]["id"], member["user"]["email"], member["user"]["name"]) == (
        str(dev),
        "dev@example.com",
        "Dev",
    )
    await add_member(admin, "platform", ops)

    r = await admin.get("/api/teams/platform/members")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(m["user"]["email"], m["source"]) for m in body["items"]] == [
        ("dev@example.com", "manual"),
        ("ops@example.com", "manual"),
    ]
    assert body["next_cursor"] is None

    assert (await admin.delete(f"/api/teams/platform/members/{dev}")).status_code == 204
    r = await admin.get("/api/teams/platform/members")
    assert [m["user"]["email"] for m in r.json()["items"]] == ["ops@example.com"]
    assert_error(await admin.delete(f"/api/teams/platform/members/{dev}"), 404, "not_found")


async def test_duplicate_member_is_409_and_unknown_user_404(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    await create_team(admin)
    dev = await seed_user(app, "dev@example.com", role="viewer")
    await add_member(admin, "platform", dev)
    r = await admin.post("/api/teams/platform/members", json={"user_id": str(dev)})
    assert_error(r, 409, "key_conflict")
    r = await admin.post("/api/teams/platform/members", json={"user_id": str(uuid.uuid4())})
    assert_error(r, 404, "not_found")
    r = await admin.post("/api/teams/nope/members", json={"user_id": str(dev)})
    assert_error(r, 404, "not_found")


async def test_members_get_the_teams_rights(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_project(admin)
    async with app.state.sessionmaker() as session:
        project_id = await session.scalar(select(Project.id).where(Project.key == "demo"))
    assert project_id is not None
    team = await create_team(admin)
    r = await admin.post(
        "/api/bindings",
        json={
            "subject_kind": "team",
            "subject_id": team["id"],
            "role": "viewer",
            "scope": "project",
            "project": "demo",
        },
    )
    assert r.status_code == 201, r.text
    async with app.state.sessionmaker() as session:
        dev = (await create_user(session, "dev@example.com", "Dev", None)).id
        other = (await create_user(session, "other@example.com", "Other", None)).id
        await session.commit()

    def principal(user_id: uuid.UUID) -> Principal:
        return Principal(
            kind=PrincipalKind.USER,
            id=user_id,
            label=str(user_id),
            auth_method="password",
            user_id=user_id,
        )

    async with app.state.sessionmaker() as session:
        before = await load_authorizer(session, principal(dev))
    assert not before.can(Permission.PROJECT_READ, project_id)

    await add_member(admin, "platform", dev)
    async with app.state.sessionmaker() as session:
        member = await load_authorizer(session, principal(dev))
        outsider = await load_authorizer(session, principal(other))
    assert member.can(Permission.PROJECT_READ, project_id)
    assert not member.can(Permission.FLAG_CREATE, project_id)
    assert not outsider.can(Permission.PROJECT_READ, project_id)


# The last-admin guard


async def admin_team_with_only_admin(app: FastAPI, admin: httpx.AsyncClient) -> uuid.UUID:
    """A team holding admin at org scope whose single member is the only admin."""
    team = await create_team(admin, "admins", "Admins")
    await seed_binding(app, SubjectKind.TEAM, uuid.UUID(team["id"]), "admin")
    user_id = await seed_user(app, "boss@example.com", role="viewer")
    await add_member(admin, "admins", user_id)
    return user_id


async def test_cannot_remove_the_only_admin_from_the_team(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    user_id = await admin_team_with_only_admin(app, admin)
    r = await admin.delete(f"/api/teams/admins/members/{user_id}")
    assert_error(r, 409, "last_admin")
    r = await admin.get("/api/teams/admins/members")
    assert [m["user"]["id"] for m in r.json()["items"]] == [str(user_id)]
    assert await audit_entries(app, "team.member.remove") == []


async def test_cannot_delete_the_team_of_the_only_admin(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    await admin_team_with_only_admin(app, admin)
    assert_error(await admin.delete("/api/teams/admins"), 409, "last_admin")
    assert (await admin.get("/api/teams/admins")).status_code == 200
    assert await audit_entries(app, "team.delete") == []


async def test_can_remove_a_team_admin_when_another_admin_remains(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    user_id = await admin_team_with_only_admin(app, admin)
    await seed_user(app, "second@example.com")
    assert (await admin.delete(f"/api/teams/admins/members/{user_id}")).status_code == 204
    assert (await admin.delete("/api/teams/admins")).status_code == 204


# Permissions


async def test_viewer_reads_but_cannot_write(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_team(admin)
    dev = await seed_user(app, "dev@example.com", role="viewer")
    async with client_with(app, await seed_service_token(app, "viewer", name="ci")) as viewer:
        assert (await viewer.get("/api/teams")).status_code == 200
        assert (await viewer.get("/api/teams/platform")).status_code == 200
        assert (await viewer.get("/api/teams/platform/members")).status_code == 200
        r = await viewer.post("/api/teams", json={"key": "other", "name": "Other"})
        assert_error(r, 403, "forbidden")
        r = await viewer.patch("/api/teams/platform", json={"name": "x"})
        assert_error(r, 403, "forbidden")
        assert_error(await viewer.delete("/api/teams/platform"), 403, "forbidden")
        r = await viewer.post("/api/teams/platform/members", json={"user_id": str(dev)})
        assert_error(r, 403, "forbidden")
        r = await viewer.delete(f"/api/teams/platform/members/{dev}")
        assert_error(r, 403, "forbidden")


async def test_teams_need_credentials(api: httpx.AsyncClient) -> None:
    assert_error(await api.get("/api/teams"), 401, "unauthorized")


# Audit


async def test_every_write_is_audited(app: FastAPI, admin: httpx.AsyncClient) -> None:
    team = await create_team(admin)
    dev = await seed_user(app, "dev@example.com", role="viewer")
    await admin.patch("/api/teams/platform", json={"name": "Platform team"})
    member = await add_member(admin, "platform", dev)
    assert (await admin.delete(f"/api/teams/platform/members/{dev}")).status_code == 204
    assert (await admin.delete("/api/teams/platform")).status_code == 204

    entries = [e for e in await audit_entries(app) if e["action"].startswith("team.")]
    assert [(e["action"], e["resource_type"], e["resource_key"]) for e in entries] == [
        ("team.create", "team", "platform"),
        ("team.update", "team", "platform"),
        ("team.member.add", "team", "platform"),
        ("team.member.remove", "team", "platform"),
        ("team.delete", "team", "platform"),
    ]
    create, update, add, remove, delete = entries
    assert (create["before"], create["after"]) == (None, team)
    assert update["before"] == team
    assert update["after"] == {**team, "name": "Platform team"}
    assert (add["before"], add["after"]) == (None, member)
    assert (remove["before"], remove["after"]) == (member, None)
    assert delete["before"] == {**team, "name": "Platform team"}
    assert delete["after"] is None

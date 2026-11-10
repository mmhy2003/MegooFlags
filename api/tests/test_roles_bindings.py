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
from sqlalchemy import update

from megooflags_api.auth.rbac import Permission
from megooflags_api.models import Team, TeamMember, User
from megooflags_api.models.enums import SubjectKind, UserStatus

FLAG_OPS = {
    "key": "flag-ops",
    "name": "Flag ops",
    "description": "Toggles flags",
    "permissions": ["flag.toggle", "project.read"],
}


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


async def create_role(client: httpx.AsyncClient, key: str, permissions: list[str]) -> None:
    r = await client.post(
        "/api/roles", json={"key": key, "name": key.title(), "permissions": permissions}
    )
    assert r.status_code == 201, r.text


async def bindings_of(client: httpx.AsyncClient, subject_id: uuid.UUID) -> list[dict[str, Any]]:
    r = await client.get("/api/bindings", params={"subject_id": str(subject_id)})
    assert r.status_code == 200, r.text
    return r.json()["items"]  # type: ignore[no-any-return]


async def principal_id(client: httpx.AsyncClient) -> uuid.UUID:
    r = await client.get("/api/auth/me")
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


# Roles


async def test_list_shows_the_builtin_roles(admin: httpx.AsyncClient) -> None:
    r = await admin.get("/api/roles")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [role["key"] for role in body["items"]] == ["admin", "editor", "viewer"]
    assert body["next_cursor"] is None
    assert all(role["builtin"] for role in body["items"])
    assert body["items"][0]["permissions"] == [p.value for p in Permission]


async def test_custom_role_create_update_delete_are_audited(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    r = await admin.post("/api/roles", json=FLAG_OPS)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["key"] == "flag-ops"
    assert created["builtin"] is False
    # Stored in catalogue order, whatever order they were sent in.
    assert created["permissions"] == ["project.read", "flag.toggle"]
    assert (await admin.get("/api/roles/flag-ops")).json() == created

    r = await admin.patch(
        "/api/roles/flag-ops", json={"name": "Flag operators", "permissions": ["flag.toggle"]}
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["name"] == "Flag operators"
    assert updated["description"] == "Toggles flags"
    assert updated["permissions"] == ["flag.toggle"]

    r = await admin.delete("/api/roles/flag-ops")
    assert r.status_code == 204, r.text
    assert_error(await admin.get("/api/roles/flag-ops"), 404, "not_found")

    [create] = await audit_entries(app, "role.create")
    assert (create["resource_type"], create["resource_key"]) == ("role", "flag-ops")
    assert create["before"] is None
    assert create["after"] == created
    [upd] = await audit_entries(app, "role.update")
    assert upd["before"] == created
    assert upd["after"] == updated
    [delete] = await audit_entries(app, "role.delete")
    assert delete["before"] == updated
    assert delete["after"] is None
    assert delete["actor_kind"] == "service_token"


async def test_builtin_roles_are_read_only(admin: httpx.AsyncClient) -> None:
    assert_error(await admin.patch("/api/roles/admin", json={"name": "x"}), 409, "builtin_role")
    assert_error(await admin.delete("/api/roles/admin"), 409, "builtin_role")
    assert_error(await admin.delete("/api/roles/viewer"), 409, "builtin_role")


async def test_role_permissions_are_checked_against_the_catalogue(
    admin: httpx.AsyncClient,
) -> None:
    for permissions in (["flag.toggle", "flag.fly"], []):
        r = await admin.post("/api/roles", json={**FLAG_OPS, "permissions": permissions})
        assert_error(r, 422, "validation_error")
    await create_role(admin, "flag-ops", ["flag.toggle"])
    r = await admin.patch("/api/roles/flag-ops", json={"permissions": ["nope"]})
    assert_error(r, 422, "validation_error")


async def test_duplicate_role_key_is_409(admin: httpx.AsyncClient) -> None:
    await create_role(admin, "flag-ops", ["flag.toggle"])
    assert_error(await admin.post("/api/roles", json=FLAG_OPS), 409, "key_conflict")
    r = await admin.post("/api/roles", json={**FLAG_OPS, "key": "admin"})
    assert_error(r, 409, "key_conflict")


async def test_deleting_a_role_in_use_is_409(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_role(admin, "flag-ops", ["flag.toggle"])
    user_id = await seed_user(app, "dev@example.com", role="flag-ops")
    body = assert_error(await admin.delete("/api/roles/flag-ops"), 409, "in_use")
    assert body["details"]["used_by"] == [
        {"kind": "binding", "subject_kind": "user", "subject_id": str(user_id)}
    ]
    assert (await admin.get("/api/roles/flag-ops")).status_code == 200


async def test_viewer_cannot_write_roles(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_role(admin, "flag-ops", ["flag.toggle"])
    async with client_with(app, await seed_service_token(app, "viewer")) as viewer:
        assert (await viewer.get("/api/roles")).status_code == 200
        assert (await viewer.get("/api/roles/flag-ops")).status_code == 200
        r = await viewer.post("/api/roles", json={**FLAG_OPS, "key": "other"})
        assert_error(r, 403, "forbidden")
        assert_error(
            await viewer.patch("/api/roles/flag-ops", json={"name": "x"}), 403, "forbidden"
        )
        assert_error(await viewer.delete("/api/roles/flag-ops"), 403, "forbidden")


async def test_roles_need_credentials(anon: httpx.AsyncClient) -> None:
    assert_error(await anon.get("/api/roles"), 401, "unauthorized")
    assert_error(await anon.get("/api/bindings"), 401, "unauthorized")


# Bindings


def project_binding(subject_id: uuid.UUID, **extra: Any) -> dict[str, Any]:
    return {
        "subject_kind": "user",
        "subject_id": str(subject_id),
        "role": "editor",
        "scope": "project",
        "project": "demo",
        **extra,
    }


async def test_create_project_binding(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_project(admin)
    user_id = await seed_user(app, "dev@example.com", role="viewer")

    r = await admin.post("/api/bindings", json=project_binding(user_id))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["subject_kind"] == "user"
    assert body["subject_id"] == str(user_id)
    assert body["subject_label"] == "dev@example.com"
    assert (body["role"], body["scope"], body["project"], body["env"]) == (
        "editor",
        "project",
        "demo",
        None,
    )
    assert body["source"] == "manual"


async def test_create_environment_binding(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_project(admin)
    user_id = await seed_user(app, "dev@example.com", role="viewer")
    binding = project_binding(user_id, scope="environment", env="production")
    r = await admin.post("/api/bindings", json=binding)
    assert r.status_code == 201, r.text
    assert (r.json()["project"], r.json()["env"]) == ("demo", "production")
    r = await admin.post("/api/bindings", json={**binding, "env": "nope"})
    assert_error(r, 404, "not_found")


async def test_binding_scope_fields_must_match(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_project(admin)
    user_id = await seed_user(app, "dev@example.com", role="viewer")
    for bad in (
        {"project": None},
        {"env": "production"},
        {"scope": "org"},
        {"scope": "environment"},
    ):
        r = await admin.post("/api/bindings", json=project_binding(user_id, **bad))
        assert_error(r, 422, "validation_error")


async def test_binding_references_must_exist(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_project(admin)
    user_id = await seed_user(app, "dev@example.com", role="viewer")
    r = await admin.post("/api/bindings", json=project_binding(user_id, project="nope"))
    assert_error(r, 404, "not_found")
    r = await admin.post("/api/bindings", json=project_binding(user_id, role="nope"))
    assert_error(r, 404, "not_found")
    for kind in ("user", "team", "service_token"):
        r = await admin.post("/api/bindings", json=project_binding(uuid.uuid4(), subject_kind=kind))
        assert_error(r, 404, "not_found")


async def test_duplicate_binding_is_409(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_project(admin)
    user_id = await seed_user(app, "dev@example.com", role="viewer")
    assert (await admin.post("/api/bindings", json=project_binding(user_id))).status_code == 201
    r = await admin.post("/api/bindings", json=project_binding(user_id))
    assert_error(r, 409, "key_conflict")


async def test_list_filters_by_subject_and_project(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_project(admin)
    await create_project(admin, "other")
    dev = await seed_user(app, "dev@example.com", role="viewer")
    ops = await seed_user(app, "ops@example.com", role="viewer")
    for subject, project in ((dev, "demo"), (dev, "other"), (ops, "demo")):
        r = await admin.post("/api/bindings", json=project_binding(subject, project=project))
        assert r.status_code == 201, r.text

    r = await admin.get("/api/bindings", params={"subject_kind": "user", "subject_id": str(dev)})
    assert sorted((b["scope"], b["project"]) for b in r.json()["items"]) == [
        ("org", None),
        ("project", "demo"),
        ("project", "other"),
    ]
    r = await admin.get("/api/bindings", params={"project": "demo"})
    assert sorted(b["subject_label"] for b in r.json()["items"]) == [
        "dev@example.com",
        "ops@example.com",
    ]
    r = await admin.get("/api/bindings", params={"project": "nope"})
    assert_error(r, 404, "not_found")


async def test_list_pages(app: FastAPI, admin: httpx.AsyncClient) -> None:
    for i in range(3):
        await seed_user(app, f"u{i}@example.com", role="viewer")
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        body = (await admin.get("/api/bindings", params=params)).json()
        seen += [b["id"] for b in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    # Three users plus the admin token.
    assert len(seen) == len(set(seen)) == 4


async def test_delete_binding_is_audited(app: FastAPI, admin: httpx.AsyncClient) -> None:
    await create_project(admin)
    user_id = await seed_user(app, "dev@example.com", role="viewer")
    created = (await admin.post("/api/bindings", json=project_binding(user_id))).json()

    r = await admin.delete(f"/api/bindings/{created['id']}")
    assert r.status_code == 204, r.text
    assert_error(await admin.delete(f"/api/bindings/{created['id']}"), 404, "not_found")
    assert [b["scope"] for b in await bindings_of(admin, user_id)] == ["org"]

    [create] = await audit_entries(app, "binding.create")
    assert (create["resource_type"], create["resource_key"]) == ("binding", created["id"])
    assert create["after"] == created
    assert create["project_id"] is not None
    [delete] = await audit_entries(app, "binding.delete")
    assert delete["before"] == created
    assert delete["after"] is None
    assert delete["project_id"] == create["project_id"]


async def test_member_manage_binds_users_but_not_tokens(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    await create_project(admin)
    await create_role(admin, "members", ["member.manage"])
    user_id = await seed_user(app, "dev@example.com", role="viewer")
    async with client_with(app, await seed_service_token(app, "viewer", name="ci")) as ci:
        token_id = await principal_id(ci)
    async with client_with(app, await seed_service_token(app, "members")) as members:
        r = await members.post(
            "/api/bindings",
            json=project_binding(token_id, subject_kind="service_token"),
        )
        assert_error(r, 403, "forbidden")
        r = await members.post("/api/bindings", json=project_binding(user_id))
        assert r.status_code == 201, r.text
        assert r.json()["subject_label"] == "dev@example.com"
        # Deleting needs the same permission as creating.
        [token_binding] = await bindings_of(admin, token_id)
        assert_error(await members.delete(f"/api/bindings/{token_binding['id']}"), 403, "forbidden")
        assert (await members.delete(f"/api/bindings/{r.json()['id']}")).status_code == 204


async def test_without_manage_rights_the_list_shows_own_bindings(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    await seed_user(app, "dev@example.com", role="viewer")
    async with client_with(app, await seed_service_token(app, "viewer", name="ci")) as ci:
        ci_id = await principal_id(ci)
        items = (await ci.get("/api/bindings")).json()["items"]
        assert [(b["subject_id"], b["role"], b["subject_label"]) for b in items] == [
            (str(ci_id), "viewer", "ci")
        ]
    # An admin sees them all: the admin token, the user and the ci token.
    assert len((await admin.get("/api/bindings")).json()["items"]) == 3


# The last-admin guard


async def test_cannot_unbind_the_only_active_admin(app: FastAPI, admin: httpx.AsyncClient) -> None:
    user_id = await seed_user(app)
    [binding] = await bindings_of(admin, user_id)
    assert_error(await admin.delete(f"/api/bindings/{binding['id']}"), 409, "last_admin")
    assert await bindings_of(admin, user_id) == [binding]
    assert await audit_entries(app, "binding.delete") == []


async def test_can_unbind_an_admin_when_another_remains(
    app: FastAPI, admin: httpx.AsyncClient
) -> None:
    user_id = await seed_user(app)
    await seed_user(app, "second@example.com")
    [binding] = await bindings_of(admin, user_id)
    assert (await admin.delete(f"/api/bindings/{binding['id']}")).status_code == 204


async def test_a_disabled_admin_does_not_count(app: FastAPI, admin: httpx.AsyncClient) -> None:
    user_id = await seed_user(app)
    second = await seed_user(app, "second@example.com")
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(User).where(User.id == second).values(status=UserStatus.DISABLED)
        )
        await session.commit()
    [binding] = await bindings_of(admin, user_id)
    assert_error(await admin.delete(f"/api/bindings/{binding['id']}"), 409, "last_admin")


async def test_admin_through_a_team_counts(app: FastAPI, admin: httpx.AsyncClient) -> None:
    user_id = await seed_user(app)
    second = await seed_user(app, "second@example.com", role="viewer")
    async with app.state.sessionmaker() as session:
        team = Team(key="admins", name="Admins")
        session.add(team)
        await session.flush()
        session.add(TeamMember(team_id=team.id, user_id=second))
        await session.commit()
    team_binding = await seed_binding(app, SubjectKind.TEAM, team.id, "admin")

    [binding] = await bindings_of(admin, user_id)
    assert (await admin.delete(f"/api/bindings/{binding['id']}")).status_code == 204
    # Now the team holds the only admin binding.
    assert_error(await admin.delete(f"/api/bindings/{team_binding}"), 409, "last_admin")

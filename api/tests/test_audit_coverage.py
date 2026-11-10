"""Every mutating `/api` route writes an audit entry with the right actor, IP and action.

A new mutating route fails `test_every_mutating_route_has_a_sample` until it gets a sample here.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from helpers import (
    ORIGIN,
    audit_entries,
    bearer,
    create_flag,
    create_project,
    create_sdk_key,
    seed_service_token,
    seed_user,
    sign_in,
)
from sqlalchemy import select

from megooflags_api.main import create_app
from megooflags_api.models import ApiToken, Environment, Project
from megooflags_api.models.enums import InvitationPurpose
from megooflags_api.services import invitations, users

CLIENT_IP = "203.0.113.10"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# POSTs that change nothing, so they are not audited ("reads aren't audited").
NOT_AUDITED = {("POST", "/api/projects/{project}/environments/{env}/evaluate")}

Params = dict[str, str]
Prepare = Callable[[httpx.AsyncClient, FastAPI], Awaitable[Params]]
Call = Callable[[httpx.AsyncClient, Params], Awaitable[httpx.Response]]


@dataclass(frozen=True)
class Sample:
    prepare: Prepare
    call: Call
    action: str
    client: Literal["admin", "anon", "session"] = "admin"


def request(method: str, template: str, body: Any = None) -> Call:
    async def call(client: httpx.AsyncClient, params: Params) -> httpx.Response:
        return await client.request(method, template.format(**params), json=body)

    return call


def sample(
    method: str,
    template: str,
    action: str,
    prepare: Prepare,
    body: Any = None,
    client: Literal["admin", "anon", "session"] = "admin",
) -> tuple[tuple[str, str], Sample]:
    return (method, template), Sample(prepare, request(method, template, body), action, client)


# Path params of the prepared resources.
DEMO = {"project": "demo", "env": "production"}


async def nothing(client: httpx.AsyncClient, app: FastAPI) -> Params:
    return {}


async def project(client: httpx.AsyncClient, app: FastAPI) -> Params:
    await create_project(client)
    return dict(DEMO)


async def flag(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await project(client, app)
    await create_flag(
        client,
        key="color",
        type="string",
        variations=[
            {"key": "a", "value": "red"},
            {"key": "b", "value": "green"},
            {"key": "c", "value": "blue"},
        ],
        off_variation="b",
    )
    # "a" is the fallthrough and "b" the off variation; "c" is unused.
    return params | {"flag": "color", "variation": "c"}


async def archived_flag(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await flag(client, app)
    r = await client.post("/api/projects/demo/flags/color/archive")
    assert r.status_code == 200, r.text
    return params


async def segment(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await project(client, app)
    r = await client.post("/api/projects/demo/segments", json={"key": "beta", "name": "Beta"})
    assert r.status_code == 201, r.text
    return params | {"segment": "beta"}


async def sdk_key(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await project(client, app)
    return params | {"id": (await create_sdk_key(client, "server"))["id"]}


async def role(client: httpx.AsyncClient, app: FastAPI) -> Params:
    r = await client.post(
        "/api/roles", json={"key": "flag-ops", "name": "Flag ops", "permissions": ["flag.toggle"]}
    )
    assert r.status_code == 201, r.text
    return {"key": "flag-ops"}


async def user(client: httpx.AsyncClient, app: FastAPI) -> Params:
    return {"id": str(await seed_user(app, "dev@example.com", role="viewer"))}


async def disabled_user(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await user(client, app)
    r = await client.post(f"/api/users/{params['id']}/disable")
    assert r.status_code == 200, r.text
    return params


async def binding(client: httpx.AsyncClient, app: FastAPI) -> Params:
    await project(client, app)
    user_id = (await user(client, app))["id"]
    r = await client.post("/api/bindings", json=binding_body(user_id))
    assert r.status_code == 201, r.text
    return {"id": r.json()["id"], "user_id": user_id}


def binding_body(user_id: str) -> dict[str, Any]:
    return {
        "subject_kind": "user",
        "subject_id": user_id,
        "role": "editor",
        "scope": "project",
        "project": "demo",
    }


async def team(client: httpx.AsyncClient, app: FastAPI) -> Params:
    r = await client.post("/api/teams", json={"key": "platform", "name": "Platform"})
    assert r.status_code == 201, r.text
    return {"key": "platform"} | await user(client, app)


async def team_member(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await team(client, app)
    r = await client.post("/api/teams/platform/members", json={"user_id": params["id"]})
    assert r.status_code == 201, r.text
    return params | {"user_id": params["id"]}


async def token(client: httpx.AsyncClient, app: FastAPI) -> Params:
    r = await client.post("/api/tokens", json={"name": "ci"})
    assert r.status_code == 201, r.text
    return {"id": r.json()["token"]["id"]}


async def invitation(client: httpx.AsyncClient, app: FastAPI) -> Params:
    async with app.state.sessionmaker() as session:
        row = await users.create_user(session, "new@example.com", "New", None)
        _, secret = await invitations.issue(session, row, InvitationPurpose.INVITE)
        await session.commit()
    return {"token": secret}


async def account(client: httpx.AsyncClient, app: FastAPI) -> Params:
    await seed_user(app)
    return {}


async def signed_in(client: httpx.AsyncClient, app: FastAPI) -> Params:
    await seed_user(app)
    assert (await sign_in(client)).status_code == 200
    return {}


async def other_session(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """Signed in twice; `id` is the session `client` is not using."""
    await signed_in(client, app)
    async with client_from(app, ORIGIN) as other:
        assert (await sign_in(other)).status_code == 200
    r = await client.get("/api/account/sessions")
    [other_id] = [item["id"] for item in r.json() if not item["current"]]
    return {"id": other_id}


async def personal_token(client: httpx.AsyncClient, app: FastAPI) -> Params:
    await signed_in(client, app)
    r = await client.post("/api/account/tokens", json=PERSONAL_TOKEN)
    assert r.status_code == 201, r.text
    return {"id": r.json()["token"]["id"]}


# Calls whose body depends on what `prepare` created.
async def create_binding(client: httpx.AsyncClient, params: Params) -> httpx.Response:
    return await client.post("/api/bindings", json=binding_body(params["user_id"]))


async def add_member(client: httpx.AsyncClient, params: Params) -> httpx.Response:
    return await client.post("/api/teams/platform/members", json={"user_id": params["id"]})


async def binding_target(client: httpx.AsyncClient, app: FastAPI) -> Params:
    await project(client, app)
    return {"user_id": (await user(client, app))["id"]}


P = "/api/projects/{project}"
E = f"{P}/environments/{{env}}"
F = f"{P}/flags/{{flag}}"
FE = f"{F}/environments/{{env}}"
S = f"{P}/segments/{{segment}}"
CONFIG = {"version": 1, "enabled": True, "fallthrough": {"variation": "b"}}
PASSWORD = "a much longer passphrase 42"
PERSONAL_TOKEN = {"name": "laptop", "permissions": ["project.read"]}

AUDIT_SAMPLES: dict[tuple[str, str], Sample] = dict(
    [
        # Setup and auth
        sample(
            "POST",
            "/api/setup",
            "auth.setup",
            nothing,
            {"email": "first@example.com", "name": "First", "password": PASSWORD},
            client="anon",
        ),
        sample(
            "POST",
            "/api/auth/sign-in",
            "auth.sign_in",
            account,
            {"email": "admin@example.com", "password": "correct horse battery staple"},
            client="anon",
        ),
        sample("POST", "/api/auth/sign-out", "auth.sign_out", signed_in, client="session"),
        # The signed-in user's account
        sample(
            "PUT",
            "/api/account/password",
            "account.password.change",
            signed_in,
            {"current_password": "correct horse battery staple", "new_password": PASSWORD},
            client="session",
        ),
        sample(
            "DELETE",
            "/api/account/sessions/{id}",
            "account.session.revoke",
            other_session,
            client="session",
        ),
        sample(
            "DELETE",
            "/api/account/sessions/others",
            "account.session.revoke",
            other_session,
            client="session",
        ),
        sample(
            "POST",
            "/api/account/tokens",
            "account.token.create",
            signed_in,
            PERSONAL_TOKEN,
            client="session",
        ),
        sample(
            "DELETE",
            "/api/account/tokens/{id}",
            "account.token.revoke",
            personal_token,
            client="session",
        ),
        sample(
            "POST",
            "/api/invitations/{token}",
            "invitation.accept",
            invitation,
            {"password": PASSWORD},
            client="anon",
        ),
        # Projects and environments
        sample("POST", "/api/projects", "project.create", nothing, {"key": "demo", "name": "Demo"}),
        sample("PATCH", P, "project.update", project, {"name": "Renamed"}),
        sample("DELETE", P, "project.delete", project),
        sample(
            "POST",
            f"{P}/environments",
            "environment.create",
            project,
            {"key": "qa", "name": "QA"},
        ),
        sample("PATCH", E, "environment.update", project, {"name": "Prod"}),
        sample("DELETE", E, "environment.delete", project),
        # Flags and variations
        sample(
            "POST",
            f"{P}/flags",
            "flag.create",
            project,
            {"key": "new-ui", "name": "New UI", "type": "boolean"},
        ),
        sample("PATCH", F, "flag.update", flag, {"name": "Colour"}),
        sample("POST", f"{F}/archive", "flag.archive", flag),
        sample("POST", f"{F}/restore", "flag.restore", archived_flag),
        sample("DELETE", F, "flag.delete", archived_flag),
        sample(
            "POST",
            f"{F}/variations",
            "flag.variation.create",
            flag,
            {"key": "d", "value": "pink"},
        ),
        sample(
            "PATCH", f"{F}/variations/{{variation}}", "flag.variation.update", flag, {"name": "R"}
        ),
        sample("DELETE", f"{F}/variations/{{variation}}", "flag.variation.delete", flag),
        # Flag config per environment
        sample("PUT", FE, "flag.config.update", flag, CONFIG),
        sample("POST", f"{FE}/toggle", "flag.toggle", flag, {"enabled": True}),
        sample("POST", f"{FE}/kill", "flag.kill", flag),
        sample("POST", f"{F}/copy", "flag.copy", flag, {"from": "staging", "to": ["production"]}),
        # Segments
        sample("POST", f"{P}/segments", "segment.create", project, {"key": "beta", "name": "B"}),
        sample("PUT", S, "segment.config.update", segment, {"included": ["alice"]}),
        sample("PATCH", S, "segment.update", segment, {"name": "Beta testers"}),
        sample("DELETE", S, "segment.delete", segment),
        # SDK keys
        sample(
            "POST",
            f"{E}/sdk-keys",
            "sdk_key.create",
            project,
            {"name": "backend", "kind": "server"},
        ),
        sample("DELETE", f"{E}/sdk-keys/{{id}}", "sdk_key.revoke", sdk_key),
        sample(
            "POST",
            f"{E}/sdk-keys/{{id}}/rotate",
            "sdk_key.rotate",
            sdk_key,
            {"overlap_seconds": 60},
        ),
        # Roles and bindings
        sample(
            "POST",
            "/api/roles",
            "role.create",
            nothing,
            {"key": "flag-ops", "name": "Flag ops", "permissions": ["flag.toggle"]},
        ),
        sample("PATCH", "/api/roles/{key}", "role.update", role, {"name": "Flag operators"}),
        sample("DELETE", "/api/roles/{key}", "role.delete", role),
        (("POST", "/api/bindings"), Sample(binding_target, create_binding, "binding.create")),
        sample("DELETE", "/api/bindings/{id}", "binding.delete", binding),
        # Users
        sample(
            "POST",
            "/api/users",
            "user.create",
            nothing,
            {"email": "dev@example.com", "name": "Dev"},
        ),
        sample("PATCH", "/api/users/{id}", "user.update", user, {"name": "Developer"}),
        sample("POST", "/api/users/{id}/disable", "user.disable", user),
        sample("POST", "/api/users/{id}/enable", "user.enable", disabled_user),
        sample("POST", "/api/users/{id}/reset-link", "user.reset_link", user),
        # Teams
        sample("POST", "/api/teams", "team.create", nothing, {"key": "platform", "name": "P"}),
        sample("PATCH", "/api/teams/{key}", "team.update", team, {"name": "Platform team"}),
        sample("DELETE", "/api/teams/{key}", "team.delete", team),
        (("POST", "/api/teams/{key}/members"), Sample(team, add_member, "team.member.add")),
        sample("DELETE", "/api/teams/{key}/members/{user_id}", "team.member.remove", team_member),
        # Service tokens
        sample("POST", "/api/tokens", "token.create", nothing, {"name": "ci"}),
        sample("DELETE", "/api/tokens/{id}", "token.revoke", token),
        sample("POST", "/api/tokens/{id}/rotate", "token.rotate", token, {"overlap_seconds": 60}),
    ]
)


def mutating_routes() -> set[tuple[str, str]]:
    return {
        (method, context.path)
        for context in iter_route_contexts(create_app().routes)
        if isinstance(context.original_route, APIRoute) and (context.path or "").startswith("/api")
        for method in (context.methods or set()) & MUTATING
    } - NOT_AUDITED


def test_every_mutating_route_has_a_sample() -> None:
    routes, samples = mutating_routes(), set(AUDIT_SAMPLES)
    missing, extra = sorted(routes - samples), sorted(samples - routes)
    assert (missing, extra) == ([], []), f"missing: {missing}\nextra: {extra}"


async def token_id(app: FastAPI) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        [row_id] = (await session.scalars(select(ApiToken.id))).all()
        return row_id


@asynccontextmanager
async def client_from(app: FastAPI, headers: dict[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, client=(CLIENT_IP, 1234))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as client:
        yield client


@pytest.mark.parametrize(
    "key", sorted(AUDIT_SAMPLES), ids=[f"{m} {p}" for m, p in sorted(AUDIT_SAMPLES)]
)
async def test_sample_writes_audit(app: FastAPI, key: tuple[str, str]) -> None:
    case = AUDIT_SAMPLES[key]
    headers = bearer(await seed_service_token(app)) if case.client == "admin" else ORIGIN
    actor_id = await token_id(app) if case.client == "admin" else None
    async with client_from(app, headers) as client:
        params = await case.prepare(client, app)
        seen = {entry["id"] for entry in await audit_entries(app)}

        r = await case.call(client, params)

    assert r.status_code < 300, r.text
    new = [entry for entry in await audit_entries(app) if entry["id"] not in seen]
    matching = [entry for entry in new if entry["action"] == case.action]
    assert matching, f"no {case.action} entry; new entries: {[e['action'] for e in new]}"
    entry = matching[0]
    assert str(entry["ip"]) == CLIENT_IP
    if case.client == "admin":
        assert entry["actor_kind"] == "service_token"
        assert entry["actor_id"] == actor_id


async def environment_id(app: FastAPI, key: str) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        env_id = await session.scalar(
            select(Environment.id)
            .join(Project, Project.id == Environment.project_id)
            .where(Project.key == "demo", Environment.key == key)
        )
        assert env_id is not None
        return env_id


async def test_flag_update_records_before_and_after(app: FastAPI, api: httpx.AsyncClient) -> None:
    await flag(api, app)

    r = await api.patch("/api/projects/demo/flags/color", json={"name": "Colour"})

    assert r.status_code == 200, r.text
    [entry] = await audit_entries(app, "flag.update")
    assert entry["resource_key"] == "color"
    assert entry["before"]["name"] == "color"
    assert entry["after"]["name"] == "Colour"
    assert entry["project_id"] is not None


async def test_flag_config_update_records_the_environment(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await flag(api, app)

    r = await api.put("/api/projects/demo/flags/color/environments/production", json=CONFIG)

    assert r.status_code == 200, r.text
    [entry] = await audit_entries(app, "flag.config.update")
    assert entry["environment_id"] == await environment_id(app, "production")
    assert entry["before"]["enabled"] is False
    assert entry["after"]["enabled"] is True
    assert entry["after"]["version"] == entry["before"]["version"] + 1


async def test_copy_records_one_entry_per_target(app: FastAPI, api: httpx.AsyncClient) -> None:
    await flag(api, app)
    staging = "/api/projects/demo/flags/color/environments/staging"
    assert (await api.put(staging, json=CONFIG)).status_code == 200

    r = await api.post(
        "/api/projects/demo/flags/color/copy",
        json={"from": "staging", "to": ["production", "development"]},
    )

    assert r.status_code == 200, r.text
    entries = await audit_entries(app, "flag.copy")
    assert {entry["environment_id"] for entry in entries} == {
        await environment_id(app, "production"),
        await environment_id(app, "development"),
    }
    for entry in entries:
        assert entry["before"]["fallthrough"] == {"variation": "a", "rollout": None}
        assert entry["after"]["fallthrough"] == {"variation": "b", "rollout": None}


async def test_sdk_key_create_stores_no_secret(app: FastAPI, api: httpx.AsyncClient) -> None:
    await project(api, app)

    created = await create_sdk_key(api, "server")

    [entry] = await audit_entries(app, "sdk_key.create")
    assert entry["resource_key"] == created["id"]
    assert entry["before"] is None
    assert set(entry["after"]) == {
        "id",
        "name",
        "kind",
        "prefix",
        "created_at",
        "revoked_at",
        "expires_at",
        "rotated_from_id",
    }
    assert created["key"] not in str(entry)

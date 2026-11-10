import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from helpers import assert_error, bearer, count_statements, seed_service_token, seed_user
from sqlalchemy import update

from megooflags_api.auth.principal import PrincipalDep
from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, authenticated, require
from megooflags_api.auth.sessions import COOKIE_PLAIN, create_session
from megooflags_api.models import Environment, Project, User
from megooflags_api.models.enums import SessionState, UserStatus

P = Permission
ORIGIN = {"Origin": "http://test"}
TOGGLE = "/t/projects/{project}/environments/{env}/toggle"


def mount_test_routes(app: FastAPI) -> None:
    router = APIRouter(prefix="/t")

    @router.get("/members")
    async def members(authz: Annotated[Authz, Depends(require(P.MEMBER_MANAGE))]) -> str:
        return authz.principal.label

    @router.post("/projects/{project}/environments/{env}/toggle")
    async def toggle(authz: Annotated[Authz, Depends(require(P.FLAG_TOGGLE))]) -> dict[str, str]:
        assert authz.project is not None and authz.environment is not None
        return {"project": authz.project.key, "env": authz.environment.key}

    @router.get("/projects/{project}")
    async def read_project(
        authz: Annotated[Authz, Depends(require(P.PROJECT_READ))],
        again: Annotated[Authz, Depends(authenticated())],
    ) -> str:
        assert authz.project is not None
        return authz.project.key

    @router.get("/me")
    async def me(authz: Annotated[Authz, Depends(authenticated())]) -> dict[str, str]:
        return {"kind": authz.principal.kind, "label": authz.principal.label}

    @router.get("/principal")
    async def principal(principal: PrincipalDep) -> dict[str, str]:
        return {"kind": principal.kind}

    app.include_router(router)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    mount_test_routes(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def seed_project(app: FastAPI, key: str, envs: list[str]) -> dict[str, uuid.UUID]:
    """A committed project with environments; returns ids keyed "project" and by env key."""
    async with app.state.sessionmaker() as session:
        project = Project(key=key, name=key)
        session.add(project)
        await session.flush()
        ids = {"project": project.id}
        for env_key in envs:
            environment = Environment(project_id=project.id, key=env_key, name=env_key)
            session.add(environment)
            await session.flush()
            ids[env_key] = environment.id
        await session.commit()
    return ids


async def seed_session(
    app: FastAPI, user_id: uuid.UUID, state: SessionState = SessionState.ACTIVE
) -> dict[str, str]:
    """A committed session for the user; returns the Cookie header that carries it."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", b"test")],
            "client": ("192.0.2.10", 5555),
            "app": app,
        }
    )
    async with app.state.sessionmaker() as session:
        user = await session.get_one(User, user_id)
        _, token = await create_session(
            session, request, user, state=state, auth_method="password", settings=app.state.settings
        )
        await session.commit()
    return {"Cookie": f"{COOKIE_PLAIN}={token}"}


async def test_no_credentials_is_401(client: httpx.AsyncClient) -> None:
    assert_error(await client.get("/t/me"), 401, "unauthorized")
    assert_error(await client.get("/t/principal"), 401, "unauthorized")


async def test_bad_bearer_is_401(client: httpx.AsyncClient) -> None:
    assert_error(
        await client.get("/t/me", headers=bearer("mf_api_" + "x" * 32)), 401, "unauthorized"
    )
    assert_error(await client.get("/t/me", headers=bearer("nope")), 401, "unauthorized")


async def test_admin_token(app: FastAPI, client: httpx.AsyncClient) -> None:
    token = await seed_service_token(app, name="ci")
    r = await client.get("/t/members", headers=bearer(token))
    assert r.status_code == 200, r.text
    assert r.json() == "ci"
    assert (await client.get("/t/me", headers=bearer(token))).json() == {
        "kind": "service_token",
        "label": "ci",
    }


async def test_viewer_cannot_manage_members(app: FastAPI, client: httpx.AsyncClient) -> None:
    token = await seed_service_token(app, "viewer")
    assert_error(await client.get("/t/members", headers=bearer(token)), 403, "forbidden")


async def test_unknown_project_is_404(app: FastAPI, client: httpx.AsyncClient) -> None:
    token = await seed_service_token(app)
    assert_error(await client.get("/t/projects/nope", headers=bearer(token)), 404, "not_found")


async def test_project_bound_elsewhere_is_404(app: FastAPI, client: httpx.AsyncClient) -> None:
    p = await seed_project(app, "p", ["prod"])
    await seed_project(app, "q", ["prod"])
    token = await seed_service_token(app, "editor", scope="project", project_id=p["project"])
    assert (await client.get("/t/projects/p", headers=bearer(token))).status_code == 200
    assert_error(await client.get("/t/projects/q", headers=bearer(token)), 404, "not_found")
    url = TOGGLE.format(project="q", env="prod")
    assert_error(await client.post(url, headers=bearer(token)), 404, "not_found")


async def test_hidden_environment_is_404(app: FastAPI, client: httpx.AsyncClient) -> None:
    p = await seed_project(app, "p", ["prod", "dev"])
    token = await seed_service_token(
        app, "editor", scope="environment", project_id=p["project"], environment_id=p["dev"]
    )
    r = await client.post(TOGGLE.format(project="p", env="dev"), headers=bearer(token))
    assert r.json() == {"project": "p", "env": "dev"}
    r = await client.post(TOGGLE.format(project="p", env="prod"), headers=bearer(token))
    assert_error(r, 404, "not_found")


async def test_visible_project_without_permission_is_403(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    p = await seed_project(app, "p", ["prod"])
    token = await seed_service_token(app, "viewer", scope="project", project_id=p["project"])
    r = await client.post(TOGGLE.format(project="p", env="prod"), headers=bearer(token))
    assert_error(r, 403, "forbidden")


async def test_cookie_get(app: FastAPI, client: httpx.AsyncClient) -> None:
    cookie = await seed_session(app, await seed_user(app, "ann@example.com"))
    r = await client.get("/t/me", headers=cookie)
    assert r.json() == {"kind": "user", "label": "ann@example.com"}
    assert (await client.get("/t/members", headers=cookie)).status_code == 200


async def test_cookie_post_needs_origin(app: FastAPI, client: httpx.AsyncClient) -> None:
    await seed_project(app, "p", ["prod"])
    cookie = await seed_session(app, await seed_user(app))
    url = TOGGLE.format(project="p", env="prod")
    assert_error(await client.post(url, headers=cookie), 403, "csrf_failed")
    assert (await client.post(url, headers={**cookie, **ORIGIN})).status_code == 200


async def test_bearer_post_needs_no_origin(app: FastAPI, client: httpx.AsyncClient) -> None:
    await seed_project(app, "p", ["prod"])
    token = await seed_service_token(app)
    r = await client.post(TOGGLE.format(project="p", env="prod"), headers=bearer(token))
    assert r.status_code == 200, r.text


async def test_pending_session_needs_mfa(app: FastAPI, client: httpx.AsyncClient) -> None:
    cookie = await seed_session(app, await seed_user(app), SessionState.PENDING_MFA)
    assert_error(await client.get("/t/me", headers=cookie), 401, "mfa_required")


async def test_enrol_session_needs_enrolment(app: FastAPI, client: httpx.AsyncClient) -> None:
    cookie = await seed_session(app, await seed_user(app), SessionState.ENROL_MFA)
    assert_error(await client.get("/t/me", headers=cookie), 403, "mfa_enrolment_required")


async def test_disabled_user_is_401(app: FastAPI, client: httpx.AsyncClient) -> None:
    user_id = await seed_user(app)
    cookie = await seed_session(app, user_id)
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(User).where(User.id == user_id).values(status=UserStatus.DISABLED)
        )
        await session.commit()
    assert_error(await client.get("/t/me", headers=cookie), 401, "unauthorized")


async def test_bindings_are_queried_once_per_request(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    await seed_project(app, "p", ["prod"])
    token = await seed_service_token(app, "viewer")
    with count_statements(app.state.engine) as statements:
        r = await client.get("/t/projects/p", headers=bearer(token))
    assert r.status_code == 200, r.text
    assert sum("role_binding" in s for s in statements) == 1
    assert sum("api_token" in s for s in statements) == 1

"""OIDC sign-in: the authorization code flow with PKCE, JIT provisioning, linking and group sync,
against the mock identity provider in `sso_fixtures`."""

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from helpers import assert_error, audit_entries, create_project, seed_user
from joserfc.jwk import RSAKey
from sqlalchemy import select, update
from sso_fixtures import (
    CLIENT_ID,
    CLIENT_SECRET,
    ISSUER,
    SSO_URL,
    OidcIdp,
    enable_sso,
    install_oidc_mock,
)

from megooflags_api.models import (
    RoleBinding,
    TeamMember,
    User,
    UserIdentity,
    UserSession,
)
from megooflags_api.models.enums import UserStatus

FAILED = "/sign-in?error=sso_failed"


@pytest.fixture
def oidc_mock(valkey_up: None, app: FastAPI) -> OidcIdp:
    enable_sso(app)
    return install_oidc_mock(app)


@pytest.fixture
async def browser(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=SSO_URL) as client:
        yield client


async def create_provider(api: httpx.AsyncClient, **extra: Any) -> dict[str, Any]:
    body = {
        "key": "kc",
        "name": "Keycloak",
        "protocol": "oidc",
        "oidc": {"issuer": ISSUER, "client_id": CLIENT_ID},
        "client_secret": CLIENT_SECRET,
        **extra,
    }
    r = await api.post("/api/sso/providers", json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


@pytest.fixture
async def provider(oidc_mock: OidcIdp, api: httpx.AsyncClient) -> dict[str, Any]:
    return await create_provider(api)


async def start(browser: httpx.AsyncClient, next: str | None = None) -> httpx.Response:
    params = {} if next is None else {"next": next}
    r = await browser.get("/api/auth/sso/kc/start", params=params)
    assert r.status_code == 302, r.text
    return r


async def callback(browser: httpx.AsyncClient, params: dict[str, str]) -> httpx.Response:
    return await browser.get("/api/auth/sso/kc/callback", params=params)


async def sign_in_sso(
    browser: httpx.AsyncClient, idp: OidcIdp, next: str | None = None
) -> httpx.Response:
    r = await start(browser, next)
    return await callback(browser, idp.authorize(r.headers["location"]))


async def user_by_email(app: FastAPI, email: str) -> User | None:
    async with app.state.sessionmaker() as session:
        return await session.scalar(select(User).where(User.email == email))


async def assert_denied(app: FastAPI, r: httpx.Response, location: str = FAILED) -> None:
    assert r.status_code == 303, r.text
    assert r.headers["location"] == location
    assert "mf_session" not in r.cookies
    denied = await audit_entries(app, "auth.sso.denied")
    assert len(denied) == 1
    assert denied[0]["resource_key"] == "kc"
    assert denied[0]["after"]["error"] == location.rsplit("=", 1)[1]


# Start


async def test_start_redirects_with_pkce_state_and_nonce(
    browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    r = await start(browser)

    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/auth"
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert query["response_type"] == "code"
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == f"{SSO_URL}/api/auth/sso/kc/callback"
    assert query["scope"] == "openid email profile"
    assert query["code_challenge_method"] == "S256"
    assert len(query["code_challenge"]) == 43
    assert len(query["state"]) >= 32
    assert len(query["nonce"]) >= 32
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("mf_sso=")
    assert "Path=/api/auth/sso" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


async def test_start_needs_a_public_url(
    valkey_up: None, app: FastAPI, api: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    await create_provider(api)

    r = await browser.get("/api/auth/sso/kc/start")

    assert_error(r, 409, "public_url_required")


async def test_start_of_an_unknown_or_disabled_provider_is_404(
    browser: httpx.AsyncClient, api: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    await api.patch("/api/sso/providers/kc", json={"enabled": False})

    assert_error(await browser.get("/api/auth/sso/kc/start"), 404, "not_found")
    assert_error(await browser.get("/api/auth/sso/nope/start"), 404, "not_found")


# Happy path


async def test_callback_signs_in_and_provisions_the_user(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: dict[str, Any]
) -> None:
    r = await sign_in_sso(browser, oidc_mock)

    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/"
    me = await browser.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "alice@example.com"
    assert me.json()["auth_method"] == "sso:kc"
    assert me.json()["session_state"] == "active"

    user = await user_by_email(app, "alice@example.com")
    assert user is not None
    assert user.password_hash is None
    assert user.name == "Alice"
    assert user.last_login_at is not None
    async with app.state.sessionmaker() as session:
        identity = await session.scalar(select(UserIdentity))
        sessions = list(await session.scalars(select(UserSession)))
    assert identity is not None
    assert (identity.subject, identity.user_id) == ("kc-subject-1", user.id)
    assert [(s.auth_method, s.mfa_verified_at) for s in sessions] == [("sso:kc", None)]

    entries = await audit_entries(app, "auth.sso.sign_in")
    assert len(entries) == 1
    assert entries[0]["actor_id"] == user.id
    assert entries[0]["auth_method"] == "sso:kc"
    assert entries[0]["resource_key"] == "alice@example.com"

    # The token request used client_secret_basic and the PKCE verifier (checked by the mock).
    assert len(oidc_mock.token_requests) == 1


async def test_second_sign_in_reuses_the_identity(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: dict[str, Any]
) -> None:
    await sign_in_sso(browser, oidc_mock)
    oidc_mock.claims["email"] = "alice@new.example.com"

    r = await sign_in_sso(browser, oidc_mock)

    assert r.status_code == 303
    assert r.headers["location"] == "/"
    async with app.state.sessionmaker() as session:
        assert len(list(await session.scalars(select(User)))) == 1
        assert len(list(await session.scalars(select(UserIdentity)))) == 1
    assert oidc_mock.jwks_requests == 1  # cached


async def test_next_is_kept(browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any) -> None:
    r = await sign_in_sso(browser, oidc_mock, next="/projects?tab=1")

    assert r.status_code == 303
    assert r.headers["location"] == "/projects?tab=1"


@pytest.mark.parametrize(
    "next", ["//evil.com", "https://evil.com/x", "/\\evil.com", "projects", "javascript:alert(1)"]
)
async def test_unsafe_next_falls_back_to_root(
    browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any, next: str
) -> None:
    r = await sign_in_sso(browser, oidc_mock, next=next)

    assert r.status_code == 303
    assert r.headers["location"] == "/"


# Verification failures


async def test_bad_signature_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    oidc_mock.signing_key = RSAKey.generate_key(2048, parameters={"kid": "k1"})

    await assert_denied(app, await sign_in_sso(browser, oidc_mock))


async def test_a_new_key_id_refetches_the_jwks(
    browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    await sign_in_sso(browser, oidc_mock)
    oidc_mock.key = RSAKey.generate_key(2048, parameters={"kid": "k2"})

    r = await sign_in_sso(browser, oidc_mock)

    assert r.headers["location"] == "/"
    assert oidc_mock.jwks_requests == 2


async def test_wrong_audience_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    oidc_mock.claims["aud"] = "someone-else"

    await assert_denied(app, await sign_in_sso(browser, oidc_mock))


async def test_wrong_issuer_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    oidc_mock.claims["iss"] = "https://evil.example.test"

    await assert_denied(app, await sign_in_sso(browser, oidc_mock))


async def test_nonce_mismatch_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    oidc_mock.nonce = "another-nonce"

    await assert_denied(app, await sign_in_sso(browser, oidc_mock))


async def test_expired_token_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    oidc_mock.claims["exp"] = int(time.time()) - 120

    await assert_denied(app, await sign_in_sso(browser, oidc_mock))


async def test_unknown_state_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    r = await start(browser)
    params = oidc_mock.authorize(r.headers["location"])

    await assert_denied(app, await callback(browser, {**params, "state": "made-up"}))


async def test_reused_state_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    r = await start(browser)
    params = oidc_mock.authorize(r.headers["location"])
    first = await callback(browser, params)
    assert first.headers["location"] == "/"

    await assert_denied(app, await callback(browser, params))


async def test_missing_cookie_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    r = await start(browser)
    params = oidc_mock.authorize(r.headers["location"])
    browser.cookies.clear()

    await assert_denied(app, await callback(browser, params))


async def test_another_browsers_cookie_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    r = await start(browser)
    params = oidc_mock.authorize(r.headers["location"])
    browser.cookies.clear()
    await start(browser)  # a new mf_sso cookie, for another sign-in

    await assert_denied(app, await callback(browser, params))


async def test_idp_error_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    r = await start(browser)
    state = oidc_mock.authorize(r.headers["location"])["state"]

    await assert_denied(app, await callback(browser, {"error": "access_denied", "state": state}))


# Linking and access


async def test_local_email_clash_is_refused(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    await seed_user(app, "alice@example.com", role="viewer")

    r = await sign_in_sso(browser, oidc_mock)

    await assert_denied(app, r, "/sign-in?error=sso_email_conflict")
    async with app.state.sessionmaker() as session:
        assert await session.scalar(select(UserIdentity)) is None


async def test_verified_email_links_when_allowed(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, api: httpx.AsyncClient
) -> None:
    await create_provider(api, link_by_verified_email=True)
    user_id = await seed_user(app, "Alice@Example.com", role="viewer")

    r = await sign_in_sso(browser, oidc_mock)

    assert r.status_code == 303
    assert r.headers["location"] == "/"
    async with app.state.sessionmaker() as session:
        identity = await session.scalar(select(UserIdentity))
    assert identity is not None and identity.user_id == user_id


async def test_unverified_email_does_not_link(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, api: httpx.AsyncClient
) -> None:
    await create_provider(api, link_by_verified_email=True)
    await seed_user(app, "alice@example.com", role="viewer")
    oidc_mock.claims["email_verified"] = False

    r = await sign_in_sso(browser, oidc_mock)

    await assert_denied(app, r, "/sign-in?error=sso_email_conflict")


async def test_disabled_user_is_refused(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    await sign_in_sso(browser, oidc_mock)
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(User).where(User.email == "alice@example.com").values(status=UserStatus.DISABLED)
        )
        await session.commit()
    browser.cookies.clear()

    r = await sign_in_sso(browser, oidc_mock)

    await assert_denied(app, r, "/sign-in?error=sso_user_disabled")


async def test_without_jit_an_unknown_user_is_refused(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, api: httpx.AsyncClient
) -> None:
    await create_provider(api, jit_enabled=False)

    r = await sign_in_sso(browser, oidc_mock)

    await assert_denied(app, r, "/sign-in?error=sso_not_provisioned")
    assert await user_by_email(app, "alice@example.com") is None


async def test_jit_needs_a_verified_email(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, provider: Any
) -> None:
    oidc_mock.claims["email_verified"] = False

    r = await sign_in_sso(browser, oidc_mock)

    await assert_denied(app, r)
    assert await user_by_email(app, "alice@example.com") is None


# Group sync


async def memberships(app: FastAPI, user_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    async with app.state.sessionmaker() as session:
        rows = await session.execute(
            select(TeamMember.team_id, TeamMember.source).where(TeamMember.user_id == user_id)
        )
        return [(team_id, source) for team_id, source in rows]


async def bindings(app: FastAPI, user_id: uuid.UUID) -> list[RoleBinding]:
    async with app.state.sessionmaker() as session:
        return list(
            await session.scalars(select(RoleBinding).where(RoleBinding.subject_id == user_id))
        )


@pytest.fixture
async def mapped(oidc_mock: OidcIdp, api: httpx.AsyncClient) -> dict[str, Any]:
    """Provider `kc` with group `devs` mapped to team `t` and to editor on project `demo`."""
    project = await create_project(api)
    r = await api.post("/api/teams", json={"key": "t", "name": "T"})
    assert r.status_code == 201, r.text
    team = r.json()
    await create_provider(api)
    for mapping in (
        {"group": "devs", "team": "t"},
        {"group": "devs", "role": "editor", "scope": "project", "project": "demo"},
    ):
        r = await api.post("/api/sso/providers/kc/mappings", json=mapping)
        assert r.status_code == 201, r.text
    return {"project": project, "team": team}


async def test_groups_map_to_teams_and_roles(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, mapped: dict[str, Any]
) -> None:
    oidc_mock.claims["groups"] = ["devs", "unmapped"]

    r = await sign_in_sso(browser, oidc_mock)

    assert r.headers["location"] == "/"
    user = await user_by_email(app, "alice@example.com")
    assert user is not None
    assert await memberships(app, user.id) == [(uuid.UUID(mapped["team"]["id"]), "sso:kc")]
    [binding] = await bindings(app, user.id)
    assert (binding.scope, binding.environment_id, binding.source) == ("project", None, "sso:kc")
    assert binding.project_id is not None
    projects = await browser.get("/api/projects")
    assert [p["key"] for p in projects.json()["items"]] == ["demo"]

    [entry] = await audit_entries(app, "sso.sync")
    assert entry["actor_id"] == user.id
    assert entry["resource_type"] == "user"
    assert entry["resource_key"] == "alice@example.com"
    assert entry["before"] == {"teams": [], "bindings": []}
    assert entry["after"] == {
        "teams": ["t"],
        "bindings": [{"role": "editor", "scope": "project", "project": "demo", "env": None}],
    }


async def test_lost_groups_are_removed_but_manual_memberships_stay(
    app: FastAPI,
    api: httpx.AsyncClient,
    browser: httpx.AsyncClient,
    oidc_mock: OidcIdp,
    mapped: dict[str, Any],
) -> None:
    await api.post("/api/teams", json={"key": "manual", "name": "Manual"})
    oidc_mock.claims["groups"] = ["devs"]
    await sign_in_sso(browser, oidc_mock)
    user = await user_by_email(app, "alice@example.com")
    assert user is not None
    r = await api.post("/api/teams/manual/members", json={"user_id": str(user.id)})
    assert r.status_code == 201, r.text
    oidc_mock.claims["groups"] = None  # no groups claim at all

    r = await sign_in_sso(browser, oidc_mock)

    assert r.headers["location"] == "/"
    teams = await memberships(app, user.id)
    assert [source for _, source in teams] == ["manual"]
    assert await bindings(app, user.id) == []
    entries = await audit_entries(app, "sso.sync")
    assert len(entries) == 2
    assert entries[1]["after"] == {"teams": [], "bindings": []}


async def test_a_manual_membership_in_a_mapped_team_stays(
    app: FastAPI,
    api: httpx.AsyncClient,
    browser: httpx.AsyncClient,
    oidc_mock: OidcIdp,
    mapped: dict[str, Any],
) -> None:
    await sign_in_sso(browser, oidc_mock)  # no groups: provisions alice
    user = await user_by_email(app, "alice@example.com")
    assert user is not None
    r = await api.post("/api/teams/t/members", json={"user_id": str(user.id)})
    assert r.status_code == 201, r.text
    oidc_mock.claims["groups"] = ["devs"]
    await sign_in_sso(browser, oidc_mock)
    oidc_mock.claims["groups"] = []

    await sign_in_sso(browser, oidc_mock)

    assert await memberships(app, user.id) == [(uuid.UUID(mapped["team"]["id"]), "manual")]
    assert await bindings(app, user.id) == []


async def test_sign_in_without_changes_writes_no_sync_entry(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, mapped: dict[str, Any]
) -> None:
    oidc_mock.claims["groups"] = ["devs"]
    await sign_in_sso(browser, oidc_mock)

    await sign_in_sso(browser, oidc_mock)

    assert len(await audit_entries(app, "sso.sync")) == 1
    assert len(await audit_entries(app, "auth.sso.sign_in")) == 2


async def test_default_role_is_bound_at_org(
    app: FastAPI, browser: httpx.AsyncClient, oidc_mock: OidcIdp, api: httpx.AsyncClient
) -> None:
    await create_provider(api, default_role="viewer")

    await sign_in_sso(browser, oidc_mock)

    user = await user_by_email(app, "alice@example.com")
    assert user is not None
    [binding] = await bindings(app, user.id)
    assert (binding.scope, binding.project_id, binding.source) == ("org", None, "sso:kc")
    [entry] = await audit_entries(app, "sso.sync")
    assert entry["after"]["bindings"] == [
        {"role": "viewer", "scope": "org", "project": None, "env": None}
    ]


async def test_group_sync_keeps_the_last_admin(
    app: FastAPI,
    api: httpx.AsyncClient,
    browser: httpx.AsyncClient,
    oidc_mock: OidcIdp,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await create_provider(api)
    r = await api.post(
        "/api/sso/providers/kc/mappings", json={"group": "admins", "role": "admin", "scope": "org"}
    )
    assert r.status_code == 201, r.text
    oidc_mock.claims["groups"] = ["admins"]
    await sign_in_sso(browser, oidc_mock)
    user = await user_by_email(app, "alice@example.com")
    assert user is not None
    oidc_mock.claims["groups"] = []

    r = await sign_in_sso(browser, oidc_mock)

    assert r.headers["location"] == "/"
    [binding] = await bindings(app, user.id)
    assert (binding.scope, binding.source) == ("org", "sso:kc")
    assert "would leave no active org admin" in caplog.text
    assert len(await audit_entries(app, "sso.sync")) == 1

    # With another admin, the sync removes it.
    await seed_user(app, "second@example.com")
    await sign_in_sso(browser, oidc_mock)
    assert await bindings(app, user.id) == []

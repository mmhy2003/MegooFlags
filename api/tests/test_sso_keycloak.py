"""SSO end to end against a real Keycloak, with the realm in
deploy/keycloak/megooflags-test-realm.json.

Nothing is mocked: the API fetches discovery, JWKS, tokens and SAML metadata from Keycloak, and the
tests drive Keycloak's login form with httpx. They skip when MEGOOFLAGS_KEYCLOAK_URL is unset.
"""

import html
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from helpers import audit_entries, create_project, seed_user
from sqlalchemy import select
from sso_fixtures import SSO_URL, enable_sso

from megooflags_api.models import RoleBinding, Team, TeamMember, User

PASSWORD = "Keycloak-test-1"
REALM = "megooflags-test"
OIDC_CLIENT_ID = "megooflags-oidc"
OIDC_CLIENT_SECRET = "megooflags-oidc-secret"

FORM_ACTION = re.compile(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', re.S)


def hidden_input(page: str, name: str) -> str:
    match = re.search(rf'name="{name}"\s+value="([^"]*)"', page)
    assert match is not None, f"no {name} in the page:\n{page}"
    return html.unescape(match.group(1))


@pytest.fixture
async def browser(
    valkey_up: None, keycloak_url: str, app: FastAPI
) -> AsyncIterator[httpx.AsyncClient]:
    """The browser's side on the API: the ASGI app at MEGOOFLAGS_PUBLIC_URL."""
    enable_sso(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url=SSO_URL, headers={"Origin": SSO_URL}
    ) as client:
        yield client


@pytest.fixture
async def providers(keycloak_url: str, api: httpx.AsyncClient) -> None:
    """`kc-oidc` and `kc-saml`, each mapping `flag-admins` to team `admins` and admin at org, and
    `viewers` to viewer at org."""
    realm = f"{keycloak_url}/realms/{REALM}"
    for body in (
        {
            "key": "kc-oidc",
            "name": "Keycloak OIDC",
            "protocol": "oidc",
            "oidc": {"issuer": realm, "client_id": OIDC_CLIENT_ID},
            "client_secret": OIDC_CLIENT_SECRET,
        },
        {
            "key": "kc-saml",
            "name": "Keycloak SAML",
            "protocol": "saml",
            "saml": {"idp_metadata_url": f"{realm}/protocol/saml/descriptor"},
        },
    ):
        r = await api.post("/api/sso/providers", json=body)
        assert r.status_code == 201, r.text
    r = await api.post("/api/teams", json={"key": "admins", "name": "Admins"})
    assert r.status_code == 201, r.text
    for key in ("kc-oidc", "kc-saml"):
        for mapping in (
            {"group": "flag-admins", "team": "admins"},
            {"group": "flag-admins", "role": "admin", "scope": "org"},
            {"group": "viewers", "role": "viewer", "scope": "org"},
        ):
            r = await api.post(f"/api/sso/providers/{key}/mappings", json=mapping)
            assert r.status_code == 201, r.text


async def kc_login(browser: httpx.AsyncClient, provider: str, username: str) -> httpx.Response:
    """Sign `username` in through `provider`; returns the API's response to the IdP's return."""
    r = await browser.get(f"/api/auth/sso/{provider}/start")
    assert r.status_code == 302, r.text
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as idp:
        page = await idp.get(r.headers["location"])
        assert page.status_code == 200, page.text
        # Keycloak's cookies are Secure. Browsers send them to http://localhost anyway (a secure
        # context); the standard library's cookie jar would not.
        for cookie in idp.cookies.jar:
            cookie.secure = False
        action = FORM_ACTION.search(page.text)
        assert action is not None, f"no login form in the page:\n{page.text}"
        r = await idp.post(
            html.unescape(action.group(1)),
            data={"username": username, "password": PASSWORD, "credentialId": ""},
            follow_redirects=False,
        )
    if provider == "kc-oidc":
        assert r.status_code == 302, r.text
        location = urlsplit(r.headers["location"])
        assert f"{location.scheme}://{location.netloc}" == SSO_URL, r.headers["location"]
        return await browser.get(f"{location.path}?{location.query}")
    assert r.status_code == 200, r.text
    form = {name: hidden_input(r.text, name) for name in ("SAMLResponse", "RelayState")}
    return await browser.post(f"/api/auth/sso/{provider}/saml/acs", data=form)


@asynccontextmanager
async def kc_admin(keycloak_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """A client for the realm's admin API, signed in as the master realm's admin."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{keycloak_url}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "admin",
                "password": "admin",
            },
        )
        assert r.status_code == 200, r.text
        client.base_url = httpx.URL(f"{keycloak_url}/admin/realms/{REALM}/")
        client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield client


async def kc_ids(admin: httpx.AsyncClient, username: str, group: str) -> tuple[str, str]:
    """The Keycloak ids of `username` and of `group`."""
    r = await admin.get("/users", params={"username": username, "exact": "true"})
    assert r.status_code == 200, r.text
    (user,) = r.json()
    r = await admin.get("/groups", params={"search": group, "exact": "true"})
    assert r.status_code == 200, r.text
    (found,) = [g for g in r.json() if g["name"] == group]
    return user["id"], found["id"]


async def user_by_email(app: FastAPI, email: str) -> User:
    async with app.state.sessionmaker() as session:
        user = await session.scalar(select(User).where(User.email == email))
    assert user is not None, f"no user {email}"
    return user


def signed_in(r: httpx.Response) -> None:
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/"


async def test_oidc_sign_in_maps_groups_to_a_team_and_admin(
    app: FastAPI, browser: httpx.AsyncClient, providers: None
) -> None:
    signed_in(await kc_login(browser, "kc-oidc", "alice"))

    me = await browser.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert (me.json()["email"], me.json()["auth_method"]) == ("alice@example.com", "sso:kc-oidc")
    alice = await user_by_email(app, "alice@example.com")
    async with app.state.sessionmaker() as session:
        teams = (
            await session.execute(
                select(Team.key, TeamMember.source)
                .join(TeamMember, TeamMember.team_id == Team.id)
                .where(TeamMember.user_id == alice.id)
            )
        ).all()
    assert [tuple(row) for row in teams] == [("admins", "sso:kc-oidc")]
    r = await browser.post("/api/projects", json={"key": "demo", "name": "Demo"})
    assert r.status_code == 201, r.text


async def test_saml_sign_in_maps_groups_to_viewer(
    app: FastAPI, browser: httpx.AsyncClient, providers: None
) -> None:
    signed_in(await kc_login(browser, "kc-saml", "bob"))

    me = await browser.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert (me.json()["email"], me.json()["auth_method"]) == ("bob@example.com", "sso:kc-saml")
    r = await browser.post("/api/projects", json={"key": "demo", "name": "Demo"})
    assert r.status_code == 403, r.text


async def test_jit_creates_users_without_passwords(
    app: FastAPI, browser: httpx.AsyncClient, providers: None
) -> None:
    signed_in(await kc_login(browser, "kc-oidc", "alice"))
    browser.cookies.clear()
    signed_in(await kc_login(browser, "kc-saml", "bob"))

    for email in ("alice@example.com", "bob@example.com"):
        assert (await user_by_email(app, email)).password_hash is None


async def test_removal_from_an_idp_group_drops_the_binding_at_next_sign_in(
    app: FastAPI,
    browser: httpx.AsyncClient,
    api: httpx.AsyncClient,
    keycloak_url: str,
    providers: None,
) -> None:
    await create_project(api)
    signed_in(await kc_login(browser, "kc-saml", "bob"))
    r = await browser.get("/api/projects")
    assert [p["key"] for p in r.json()["items"]] == ["demo"]
    browser.cookies.clear()

    async with kc_admin(keycloak_url) as admin:
        user_id, group_id = await kc_ids(admin, "bob", "viewers")
        r = await admin.delete(f"/users/{user_id}/groups/{group_id}")
        assert r.status_code == 204, r.text
        try:
            signed_in(await kc_login(browser, "kc-saml", "bob"))
        finally:
            r = await admin.put(f"/users/{user_id}/groups/{group_id}")
            assert r.status_code == 204, r.text

    bob = await user_by_email(app, "bob@example.com")
    async with app.state.sessionmaker() as session:
        bindings = list(
            await session.scalars(select(RoleBinding).where(RoleBinding.subject_id == bob.id))
        )
    assert bindings == []
    r = await browser.get("/api/projects")
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []


async def test_an_email_owned_by_a_local_user_is_refused(
    app: FastAPI, browser: httpx.AsyncClient, providers: None
) -> None:
    carol_id = await seed_user(app, "carol@example.com", role="viewer")

    r = await kc_login(browser, "kc-oidc", "carol")

    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/sign-in?error=sso_email_conflict"
    assert "mf_session" not in r.cookies
    (denied,) = await audit_entries(app, "auth.sso.denied")
    assert denied["resource_key"] == "kc-oidc"
    assert denied["after"]["error"] == "sso_email_conflict"
    assert (await user_by_email(app, "carol@example.com")).id == carol_id

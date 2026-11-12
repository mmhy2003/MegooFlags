"""Every mutating `/api` route writes an audit entry with the right actor, IP and action.

A new mutating route fails `test_every_mutating_route_has_a_sample` until it gets a sample here.
"""

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from helpers import (
    ORIGIN,
    PUBLIC_URL,
    audit_entries,
    bearer,
    create_flag,
    create_project,
    create_sdk_key,
    enable_passkeys,
    enrol_totp,
    register_passkey,
    seed_service_token,
    seed_user,
    sign_in,
    totp_code,
)
from lxml import etree
from onelogin.saml2.utils import OneLogin_Saml2_Utils  # type: ignore[import-untyped]
from soft_webauthn import SoftAuthenticator
from sqlalchemy import select
from sso_fixtures import SAML_KEY, enable_sso, idp_metadata, saml_response

from megooflags_api.main import create_app
from megooflags_api.models import ApiToken, Environment, Project
from megooflags_api.models.enums import InvitationPurpose
from megooflags_api.services import invitations, users

CLIENT_IP = "203.0.113.10"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# POSTs that change nothing, so they are not audited ("reads aren't audited").
NOT_AUDITED = {
    ("POST", "/api/projects/{project}/environments/{env}/evaluate"),
    # They only store a WebAuthn challenge in Valkey.
    ("POST", "/api/account/passkeys/options"),
    ("POST", "/api/auth/webauthn/options"),
}

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


async def schedule(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await flag(client, app)
    r = await client.post(
        "/api/projects/demo/flags/color/environments/production/schedules",
        json={"action": "enable", "run_at": RUN_AT},
    )
    assert r.status_code == 201, r.text
    return params | {"id": r.json()["id"]}


async def rollout(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await flag(client, app)
    r = await client.post(
        "/api/projects/demo/flags/color/environments/production/rollout", json=ROLLOUT
    )
    assert r.status_code == 201, r.text
    return params


async def paused_rollout(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await rollout(client, app)
    r = await client.post("/api/projects/demo/flags/color/environments/production/rollout/pause")
    assert r.status_code == 200, r.text
    return params


async def change_request(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """A toggle change request in a protected production."""
    params = await flag(client, app)
    r = await client.patch(
        "/api/projects/demo/environments/production", json={"approvals_required": 1}
    )
    assert r.status_code == 200, r.text
    r = await client.post("/api/projects/demo/change-requests", json=TOGGLE_CHANGE_REQUEST)
    assert r.status_code == 201, r.text
    return params | {"id": r.json()["id"]}


async def others_change_request(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """`change_request`, written by another token, so `client` may review it."""
    params = await flag(client, app)
    r = await client.patch(
        "/api/projects/demo/environments/production", json={"approvals_required": 1}
    )
    assert r.status_code == 200, r.text
    async with client_from(app, bearer(await seed_service_token(app, name="author"))) as author:
        r = await author.post("/api/projects/demo/change-requests", json=TOGGLE_CHANGE_REQUEST)
    assert r.status_code == 201, r.text
    return params | {"id": r.json()["id"]}


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


SSO_PROVIDER = {
    "key": "keycloak",
    "name": "Keycloak",
    "protocol": "oidc",
    "oidc": {"issuer": "https://idp.example.test/realms/test", "client_id": "megooflags"},
    "client_secret": "oidc-client-secret",
}


async def sso_provider(client: httpx.AsyncClient, app: FastAPI) -> Params:
    r = await client.post("/api/sso/providers", json=SSO_PROVIDER)
    assert r.status_code == 201, r.text
    return {"key": "keycloak"}


async def sso_mapping(client: httpx.AsyncClient, app: FastAPI) -> Params:
    params = await sso_provider(client, app)
    r = await client.post(
        "/api/sso/providers/keycloak/mappings",
        json={"group": "admins", "role": "admin", "scope": "org"},
    )
    assert r.status_code == 201, r.text
    return params | {"id": r.json()["id"]}


async def saml_started(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """A SAML provider, and a sign-in `client` has started: its RelayState and request ID."""
    enable_sso(app)
    async with client_from(app, bearer(await seed_service_token(app))) as admin:
        r = await admin.post(
            "/api/sso/providers",
            json={
                "key": SAML_KEY,
                "name": "SAML",
                "protocol": "saml",
                "saml": {"idp_metadata_xml": idp_metadata()},
            },
        )
        assert r.status_code == 201, r.text
    r = await client.get(f"/api/auth/sso/{SAML_KEY}/start")
    assert r.status_code == 302, r.text
    query = parse_qs(urlsplit(r.headers["location"]).query)
    request = OneLogin_Saml2_Utils.decode_base64_and_inflate(query["SAMLRequest"][0])
    return {"relay": query["RelayState"][0], "request_id": etree.fromstring(request).get("ID")}


async def saml_sign_in(client: httpx.AsyncClient, params: Params) -> httpx.Response:
    """POST a valid response to the ACS; it redirects (303), so `/auth/me` stands in for it."""
    r = await client.post(
        f"/api/auth/sso/{SAML_KEY}/saml/acs",
        data={
            "SAMLResponse": saml_response(in_response_to=params["request_id"]),
            "RelayState": params["relay"],
        },
    )
    assert (r.status_code, r.headers["location"]) == (303, "/"), r.text
    return await client.get("/api/auth/me")


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


async def totp_started(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """Signed in, with a TOTP enrolment waiting for its confirmation code."""
    await signed_in(client, app)
    r = await client.post("/api/account/totp")
    assert r.status_code == 201, r.text
    return {"secret": r.json()["secret"]}


async def totp_enrolled(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """Signed in with TOTP confirmed just now (a recent verification)."""
    await signed_in(client, app)
    secret, codes = await enrol_totp(client)
    return {"secret": secret, "recovery_code": codes[0]}


async def mfa_pending(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """Signed in with a password; the second factor is still to come."""
    params = await totp_enrolled(client, app)
    assert (await sign_in(client)).json()["status"] == "mfa_required"
    return params


async def mfa_user(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """A viewer with TOTP; `client` (an admin) is left as it was."""
    params = await user(client, app)
    async with client_from(app, ORIGIN) as other:
        assert (await sign_in(other, "dev@example.com")).status_code == 200
        await enrol_totp(other)
    return params


async def personal_token(client: httpx.AsyncClient, app: FastAPI) -> Params:
    await signed_in(client, app)
    r = await client.post("/api/account/tokens", json=PERSONAL_TOKEN)
    assert r.status_code == 201, r.text
    return {"id": r.json()["token"]["id"]}


# The authenticators of the passkeys `passkey` registered, by passkey id.
AUTHENTICATORS: dict[str, SoftAuthenticator] = {}


async def passkeys_enabled(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """Signed in, on an app with a public URL; passkey challenges need Valkey."""
    if "MEGOOFLAGS_VALKEY_URL" not in os.environ:
        pytest.skip("MEGOOFLAGS_VALKEY_URL is not set")
    enable_passkeys(app)
    return await signed_in(client, app)


async def passkey(client: httpx.AsyncClient, app: FastAPI) -> Params:
    """Signed in with a passkey registered just now (a recent verification)."""
    await passkeys_enabled(client, app)
    authenticator = SoftAuthenticator(origin=PUBLIC_URL, rp_id="flags.example.test")
    passkey_id = (await register_passkey(client, authenticator))["passkey"]["id"]
    AUTHENTICATORS[passkey_id] = authenticator
    return {"id": passkey_id}


# Calls whose body depends on what `prepare` created.
async def create_binding(client: httpx.AsyncClient, params: Params) -> httpx.Response:
    return await client.post("/api/bindings", json=binding_body(params["user_id"]))


async def add_member(client: httpx.AsyncClient, params: Params) -> httpx.Response:
    return await client.post("/api/teams/platform/members", json={"user_id": params["id"]})


async def register_new_passkey(client: httpx.AsyncClient, params: Params) -> httpx.Response:
    options = (await client.post("/api/account/passkeys/options")).json()
    authenticator = SoftAuthenticator(origin=PUBLIC_URL, rp_id="flags.example.test")
    return await client.post(
        "/api/account/passkeys",
        json={
            "challenge_id": options["challenge_id"],
            "name": "Laptop",
            "credential": authenticator.create(options["options"]),
        },
    )


async def passkey_sign_in(client: httpx.AsyncClient, params: Params) -> httpx.Response:
    options = (await client.post("/api/auth/webauthn/options")).json()
    credential = AUTHENTICATORS.pop(params["id"]).get(options["options"])
    return await client.post(
        "/api/auth/webauthn/verify",
        json={"challenge_id": options["challenge_id"], "credential": credential},
    )


def with_totp_code(path: str, *, steps: int = 0) -> Call:
    """POST `path` with the TOTP code for `steps` from now, which `prepare` cannot know yet."""

    async def call(client: httpx.AsyncClient, params: Params) -> httpx.Response:
        return await client.post(path, json={"code": totp_code(params["secret"], steps)})

    return call


async def use_recovery_code(client: httpx.AsyncClient, params: Params) -> httpx.Response:
    return await client.post("/api/auth/mfa/recovery", json={"code": params["recovery_code"]})


async def binding_target(client: httpx.AsyncClient, app: FastAPI) -> Params:
    await project(client, app)
    return {"user_id": (await user(client, app))["id"]}


P = "/api/projects/{project}"
E = f"{P}/environments/{{env}}"
F = f"{P}/flags/{{flag}}"
FE = f"{F}/environments/{{env}}"
S = f"{P}/segments/{{segment}}"
CONFIG = {"version": 1, "enabled": True, "fallthrough": {"variation": "b"}}
RUN_AT = (datetime.now(UTC) + timedelta(days=1)).isoformat()
ROLLOUT = {
    "from_variation": "a",
    "to_variation": "b",
    "steps": [{"percentage": 10000}, {"percentage": 100000}],
}
TOGGLE_CHANGE_REQUEST = {
    "environment": "production",
    "flag": "color",
    "kind": "toggle",
    "title": "Turn color on",
    "payload": {"enabled": True},
}
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
        # The confirmation used up the current step, so sign-in uses the next one.
        (
            ("POST", "/api/auth/mfa/totp"),
            Sample(
                mfa_pending,
                with_totp_code("/api/auth/mfa/totp", steps=1),
                "auth.sign_in",
                "session",
            ),
        ),
        (
            ("POST", "/api/auth/mfa/recovery"),
            Sample(mfa_pending, use_recovery_code, "auth.sign_in", "session"),
        ),
        (
            ("POST", "/api/auth/webauthn/verify"),
            Sample(passkey, passkey_sign_in, "auth.sign_in", "session"),
        ),
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
        sample("POST", "/api/account/totp", "account.totp.enrol", signed_in, client="session"),
        (
            ("POST", "/api/account/totp/confirm"),
            Sample(
                totp_started,
                with_totp_code("/api/account/totp/confirm"),
                "account.totp.confirm",
                "session",
            ),
        ),
        sample(
            "DELETE", "/api/account/totp", "account.totp.remove", totp_enrolled, client="session"
        ),
        (
            ("POST", "/api/account/passkeys"),
            Sample(passkeys_enabled, register_new_passkey, "account.passkey.register", "session"),
        ),
        sample(
            "PATCH",
            "/api/account/passkeys/{id}",
            "account.passkey.rename",
            passkey,
            {"name": "Phone"},
            client="session",
        ),
        sample(
            "DELETE",
            "/api/account/passkeys/{id}",
            "account.passkey.delete",
            passkey,
            client="session",
        ),
        sample(
            "POST",
            "/api/account/recovery-codes",
            "account.recovery_codes.regenerate",
            totp_enrolled,
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
        sample("PUT", f"{F}/lifecycle", "flag.lifecycle.update", flag, {"permanent": True}),
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
        sample(
            "POST",
            f"{FE}/schedules",
            "flag.schedule.create",
            flag,
            {"action": "enable", "run_at": RUN_AT},
        ),
        sample("DELETE", f"{FE}/schedules/{{id}}", "flag.schedule.cancel", schedule),
        sample("POST", f"{FE}/rollout", "flag.rollout.start", flag, ROLLOUT),
        sample("POST", f"{FE}/rollout/pause", "flag.rollout.pause", rollout),
        sample("POST", f"{FE}/rollout/resume", "flag.rollout.resume", paused_rollout),
        sample("POST", f"{FE}/rollout/abort", "flag.rollout.abort", rollout),
        sample("POST", f"{F}/copy", "flag.copy", flag, {"from": "staging", "to": ["production"]}),
        # Change requests
        sample(
            "POST", f"{P}/change-requests", "change_request.create", flag, TOGGLE_CHANGE_REQUEST
        ),
        sample(
            "POST",
            f"{P}/change-requests/{{id}}/comments",
            "change_request.comment",
            change_request,
            {"body": "lgtm"},
        ),
        sample(
            "POST", f"{P}/change-requests/{{id}}/cancel", "change_request.cancel", change_request
        ),
        sample(
            "POST",
            f"{P}/change-requests/{{id}}/approve",
            "change_request.approve",
            others_change_request,
        ),
        sample(
            "POST",
            f"{P}/change-requests/{{id}}/reject",
            "change_request.reject",
            others_change_request,
            {"comment": "no"},
        ),
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
        sample("DELETE", "/api/users/{id}/mfa", "user.mfa_reset", mfa_user),
        # Settings
        sample("PUT", "/api/settings", "settings.update", nothing, {"require_mfa": True}),
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
        # SSO providers and group mappings
        sample("POST", "/api/sso/providers", "sso_provider.create", nothing, SSO_PROVIDER),
        sample(
            "PATCH", "/api/sso/providers/{key}", "sso_provider.update", sso_provider, {"name": "K"}
        ),
        sample("DELETE", "/api/sso/providers/{key}", "sso_provider.delete", sso_provider),
        sample(
            "POST",
            "/api/sso/providers/{key}/mappings",
            "sso_mapping.create",
            sso_provider,
            {"group": "platform", "role": "viewer", "scope": "org"},
        ),
        sample(
            "DELETE", "/api/sso/providers/{key}/mappings/{id}", "sso_mapping.delete", sso_mapping
        ),
        (
            ("POST", "/api/auth/sso/{provider}/saml/acs"),
            Sample(saml_started, saml_sign_in, "auth.sso.sign_in", "anon"),
        ),
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

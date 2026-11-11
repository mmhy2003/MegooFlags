"""SAML 2.0 sign-in: SP metadata, the SP-initiated redirect, and the ACS with its checks, against
the locally signed responses in `sso_fixtures`."""

import base64
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from helpers import assert_error, audit_entries, create_project
from lxml import etree
from onelogin.saml2.utils import OneLogin_Saml2_Utils  # type: ignore[import-untyped]
from sqlalchemy import select
from sso_fixtures import (
    IDP_SSO_URL,
    SAML_ACS_URL,
    SAML_ENTITY_ID,
    SAML_KEY,
    SSO_URL,
    enable_sso,
    idp_metadata,
    saml_response,
    self_signed,
)

from megooflags_api.auth.sso.state import put_state
from megooflags_api.models import TeamMember, User, UserIdentity

FAILED = "/sign-in?error=sso_failed"
MD = "{urn:oasis:names:tc:SAML:2.0:metadata}"


@pytest.fixture
def saml_sso(valkey_up: None, app: FastAPI) -> None:
    enable_sso(app)


@pytest.fixture
async def browser(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=SSO_URL) as client:
        yield client


async def create_provider(api: httpx.AsyncClient, **extra: Any) -> dict[str, Any]:
    body = {
        "key": SAML_KEY,
        "name": "Keycloak SAML",
        "protocol": "saml",
        "saml": {"idp_metadata_xml": idp_metadata()},
        **extra,
    }
    r = await api.post("/api/sso/providers", json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


@pytest.fixture
async def provider(saml_sso: None, api: httpx.AsyncClient) -> dict[str, Any]:
    return await create_provider(api)


async def start(browser: httpx.AsyncClient, next: str | None = None) -> tuple[str, str]:
    """The RelayState and the AuthnRequest ID of a new sign-in."""
    params = {} if next is None else {"next": next}
    r = await browser.get(f"/api/auth/sso/{SAML_KEY}/start", params=params)
    assert r.status_code == 302, r.text
    query = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    request = etree.fromstring(OneLogin_Saml2_Utils.decode_base64_and_inflate(query["SAMLRequest"]))
    return query["RelayState"], request.get("ID")


async def acs(browser: httpx.AsyncClient, response: str, relay: str | None) -> httpx.Response:
    form = {"SAMLResponse": response}
    if relay is not None:
        form["RelayState"] = relay
    return await browser.post(f"/api/auth/sso/{SAML_KEY}/saml/acs", data=form)


async def sign_in_saml(
    browser: httpx.AsyncClient, next: str | None = None, **response: Any
) -> httpx.Response:
    relay, request_id = await start(browser, next)
    response.setdefault("in_response_to", request_id)
    return await acs(browser, saml_response(**response), relay)


async def user_by_email(app: FastAPI, email: str) -> User | None:
    async with app.state.sessionmaker() as session:
        return await session.scalar(select(User).where(User.email == email))


async def assert_denied(
    app: FastAPI, r: httpx.Response, *, signed_in_before: bool = False
) -> dict[str, Any]:
    assert r.status_code == 303, r.text
    assert r.headers["location"] == FAILED
    assert "mf_session" not in r.cookies
    [denied] = await audit_entries(app, "auth.sso.denied")
    assert denied["resource_key"] == SAML_KEY
    assert denied["after"]["error"] == "sso_failed"
    if not signed_in_before:
        assert await user_by_email(app, "alice@example.com") is None
    assert len(await audit_entries(app, "auth.sso.sign_in")) == int(signed_in_before)
    return denied


# Metadata


async def test_metadata_names_the_entity_id_and_acs(
    browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    r = await browser.get(f"/api/auth/sso/{SAML_KEY}/saml/metadata")

    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/samlmetadata+xml")
    root = etree.fromstring(r.content)
    assert root.get("entityID") == SAML_ENTITY_ID
    [acs_service] = root.iter(f"{MD}AssertionConsumerService")
    assert acs_service.get("Location") == SAML_ACS_URL
    assert acs_service.get("Binding") == "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
    [sp] = root.iter(f"{MD}SPSSODescriptor")
    assert sp.get("WantAssertionsSigned") == "true"
    assert sp.get("AuthnRequestsSigned") == "false"


async def test_metadata_with_an_sp_key_offers_its_certificate(
    browser: httpx.AsyncClient, saml_sso: None, api: httpx.AsyncClient
) -> None:
    key, cert = self_signed("megooflags.test")
    await create_provider(api, sp_private_key=key, sp_certificate=cert)

    r = await browser.get(f"/api/auth/sso/{SAML_KEY}/saml/metadata")

    root = etree.fromstring(r.content)
    [sp] = root.iter(f"{MD}SPSSODescriptor")
    assert sp.get("AuthnRequestsSigned") == "true"
    assert len(list(root.iter(f"{MD}KeyDescriptor"))) >= 1


async def test_metadata_of_an_unknown_provider_is_404(
    browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    assert_error(await browser.get("/api/auth/sso/nope/saml/metadata"), 404, "not_found")


# Start


async def test_start_redirects_with_saml_request_and_relay_state(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    r = await browser.get(f"/api/auth/sso/{SAML_KEY}/start", params={"next": "/projects"})

    assert r.status_code == 302, r.text
    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == IDP_SSO_URL
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    request = etree.fromstring(OneLogin_Saml2_Utils.decode_base64_and_inflate(query["SAMLRequest"]))
    assert request.get("AssertionConsumerServiceURL") == SAML_ACS_URL
    # No RequestedAuthnContext: the IdP picks how to authenticate (MFA, passwordless).
    assert request.find("{urn:oasis:names:tc:SAML:2.0:protocol}RequestedAuthnContext") is None
    relay = query["RelayState"]
    assert len(relay) >= 32
    stored = await app.state.valkey.get(f"megooflags:auth:sso:saml:{relay}")
    assert json.loads(stored) == {
        "provider_id": provider["id"],
        "request_id": request.get("ID"),
        "next": "/projects",
    }
    assert "mf_sso" not in r.cookies


async def test_start_with_a_signing_key_signs_the_request(
    browser: httpx.AsyncClient, saml_sso: None, api: httpx.AsyncClient
) -> None:
    key, cert = self_signed("megooflags.test")
    await create_provider(api, sp_private_key=key, sp_certificate=cert)

    r = await browser.get(f"/api/auth/sso/{SAML_KEY}/start")

    query = parse_qs(urlsplit(r.headers["location"]).query)
    assert query["SigAlg"] == ["http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"]
    assert query["Signature"]


# Happy path


async def test_valid_response_signs_in_and_provisions_the_user(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    r = await sign_in_saml(browser, next="/projects?tab=1")

    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/projects?tab=1"
    me = await browser.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "alice@example.com"
    assert me.json()["auth_method"] == f"sso:{SAML_KEY}"
    assert me.json()["session_state"] == "active"
    user = await user_by_email(app, "alice@example.com")
    assert user is not None
    assert user.name == "Alice"
    async with app.state.sessionmaker() as session:
        identity = await session.scalar(select(UserIdentity))
    assert identity is not None
    assert (identity.subject, identity.user_id) == ("alice@example.com", user.id)
    [entry] = await audit_entries(app, "auth.sso.sign_in")
    assert entry["actor_id"] == user.id


async def test_groups_are_synced(
    app: FastAPI, browser: httpx.AsyncClient, saml_sso: None, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    r = await api.post("/api/teams", json={"key": "t", "name": "T"})
    assert r.status_code == 201, r.text
    team_id = uuid.UUID(r.json()["id"])
    await create_provider(api)
    r = await api.post(
        f"/api/sso/providers/{SAML_KEY}/mappings", json={"group": "devs", "team": "t"}
    )
    assert r.status_code == 201, r.text

    r = await sign_in_saml(browser, groups=["devs", "unmapped"])

    assert r.headers["location"] == "/"
    user = await user_by_email(app, "alice@example.com")
    assert user is not None
    async with app.state.sessionmaker() as session:
        rows = await session.execute(
            select(TeamMember.team_id, TeamMember.source).where(TeamMember.user_id == user.id)
        )
        assert [tuple(row) for row in rows] == [(team_id, f"sso:{SAML_KEY}")]
    [entry] = await audit_entries(app, "sso.sync")
    assert entry["after"]["teams"] == ["t"]


async def test_name_id_is_the_email_fallback(
    app: FastAPI, browser: httpx.AsyncClient, saml_sso: None, api: httpx.AsyncClient
) -> None:
    await create_provider(
        api,
        saml={"idp_metadata_xml": idp_metadata(), "email_attribute": "mail"},
    )

    r = await sign_in_saml(browser, name_id="bob@example.com", email="ignored@example.com")

    assert r.headers["location"] == "/"
    assert await user_by_email(app, "bob@example.com") is not None


# Verification failures


async def test_tampered_signature_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    relay, request_id = await start(browser)
    xml = base64.b64decode(saml_response(in_response_to=request_id)).decode()
    tampered = xml.replace("alice@example.com", "mallory@example.com")

    r = await acs(browser, base64.b64encode(tampered.encode()).decode(), relay)

    await assert_denied(app, r)
    assert await user_by_email(app, "mallory@example.com") is None


async def test_signature_by_another_key_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    key, cert = self_signed("evil.example.test")

    await assert_denied(app, await sign_in_saml(browser, key=key, cert=cert))


async def test_unsigned_assertion_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    await assert_denied(app, await sign_in_saml(browser, sign=False))


async def test_wrong_audience_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    await assert_denied(app, await sign_in_saml(browser, audience="http://someone-else.test"))


@pytest.mark.parametrize("field", ["destination", "recipient"])
async def test_wrong_destination_or_recipient_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any], field: str
) -> None:
    other = f"{SSO_URL}/api/auth/sso/other/saml/acs"

    await assert_denied(app, await sign_in_saml(browser, **{field: other}))


async def test_in_response_to_mismatch_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    await assert_denied(app, await sign_in_saml(browser, in_response_to="_another-request"))


async def test_expired_not_on_or_after_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    expired = datetime.now(UTC) - timedelta(minutes=2)

    await assert_denied(app, await sign_in_saml(browser, not_on_or_after=expired))


async def test_unknown_relay_state_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    _, request_id = await start(browser)

    r = await acs(browser, saml_response(in_response_to=request_id), "made-up")

    await assert_denied(app, r)


async def test_missing_relay_state_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    _, request_id = await start(browser)

    await assert_denied(app, await acs(browser, saml_response(in_response_to=request_id), None))


async def test_garbage_response_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    relay, _ = await start(browser)

    await assert_denied(app, await acs(browser, "not base64 xml!", relay))


async def test_replayed_assertion_is_denied(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    relay, request_id = await start(browser)
    response = saml_response(in_response_to=request_id, assertion_id="_assertion-1")
    first = await acs(browser, response, relay)
    assert first.headers["location"] == "/"
    ttl = await app.state.valkey.ttl("megooflags:auth:sso:saml-assertion:_assertion-1")
    assert 60 < ttl <= 300
    browser.cookies.clear()
    # A fresh RelayState for the same request, so only the assertion ID gives the replay away.
    fresh = "fresh-relay-state"
    state = {"provider_id": provider["id"], "request_id": request_id, "next": "/"}
    await put_state(app.state.valkey, "saml", fresh, state)

    r = await acs(browser, response, fresh)

    denied = await assert_denied(app, r, signed_in_before=True)

    assert "replayed" in denied["after"]["reason"]


async def test_disabled_provider_is_denied_at_the_acs(
    app: FastAPI, browser: httpx.AsyncClient, api: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    relay, request_id = await start(browser)
    await api.patch(f"/api/sso/providers/{SAML_KEY}", json={"enabled": False})

    await assert_denied(app, await acs(browser, saml_response(in_response_to=request_id), relay))


async def test_start_needs_a_public_url(
    valkey_up: None, app: FastAPI, api: httpx.AsyncClient, browser: httpx.AsyncClient
) -> None:
    await create_provider(api)

    assert_error(await browser.get(f"/api/auth/sso/{SAML_KEY}/start"), 409, "public_url_required")
    r = await browser.get(f"/api/auth/sso/{SAML_KEY}/saml/metadata")
    assert_error(r, 409, "public_url_required")


async def test_assertion_expiry_bounds_the_replay_window(
    app: FastAPI, browser: httpx.AsyncClient, provider: dict[str, Any]
) -> None:
    soon = datetime.now(UTC) + timedelta(seconds=20)

    r = await sign_in_saml(browser, not_on_or_after=soon, assertion_id="_short")

    assert r.headers["location"] == "/"
    ttl = await app.state.valkey.ttl("megooflags:auth:sso:saml-assertion:_short")
    assert 50 < ttl <= 60


async def test_provider_with_a_metadata_url_signs_in(
    app: FastAPI, browser: httpx.AsyncClient, saml_sso: None, api: httpx.AsyncClient
) -> None:
    url = "https://idp.example.test/realms/test/protocol/saml/descriptor"
    app.state.sso_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=idp_metadata()))
    )
    await create_provider(api, saml={"idp_metadata_url": url})

    r = await sign_in_saml(browser)

    assert r.headers["location"] == "/"

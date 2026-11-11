import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    PUBLIC_URL,
    assert_error,
    audit_entries,
    bearer,
    create_project,
    seed_service_token,
)
from pydantic import AnyHttpUrl
from sqlalchemy import select

from megooflags_api.models import SsoGroupMapping, SsoProvider

SECRET = "oidc-client-secret-value"
SP_KEY = "-----BEGIN PRIVATE KEY-----\nsp-private-key-material\n-----END PRIVATE KEY-----"
SP_CERT = "-----BEGIN CERTIFICATE-----\nsp-certificate\n-----END CERTIFICATE-----"
METADATA = '<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="idp"/>'
METADATA_URL = "https://idp.example.test/realms/test/protocol/saml/descriptor"

OIDC = {
    "key": "keycloak",
    "name": "Keycloak",
    "protocol": "oidc",
    "oidc": {"issuer": "https://idp.example.test/realms/test", "client_id": "megooflags"},
    "client_secret": SECRET,
}
SAML = {
    "key": "kc-saml",
    "name": "Keycloak SAML",
    "protocol": "saml",
    "saml": {"idp_metadata_xml": METADATA},
}


async def create_provider(api: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    r = await api.post("/api/sso/providers", json=body)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def provider_row(app: FastAPI, key: str) -> SsoProvider:
    async with app.state.sessionmaker() as session:
        row = await session.scalar(select(SsoProvider).where(SsoProvider.key == key))
        assert row is not None
        return row


def stored_secret(app: FastAPI, row: SsoProvider) -> dict[str, str]:
    assert row.secret_encrypted is not None
    return json.loads(app.state.secret_box.decrypt(row.secret_encrypted))  # type: ignore[no-any-return]


@pytest.fixture
async def viewer(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    headers = bearer(await seed_service_token(app, "viewer", name="viewer"))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as client:
        yield client


@pytest.fixture
def metadata_requests(app: FastAPI) -> list[httpx.Request]:
    """Serves METADATA at METADATA_URL through `app.state.sso_http`; records the requests."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if str(request.url) == METADATA_URL:
            return httpx.Response(200, text=METADATA)
        return httpx.Response(404)

    app.state.sso_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return seen


async def test_create_oidc_provider_encrypts_the_secret(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    r = await api.post("/api/sso/providers", json=OIDC)

    assert r.status_code == 201, r.text
    body = r.json()
    assert SECRET not in r.text
    assert body["key"] == "keycloak"
    assert body["protocol"] == "oidc"
    assert body["enabled"] is True
    assert body["oidc"] == {
        "issuer": "https://idp.example.test/realms/test",
        "client_id": "megooflags",
        "scopes": ["openid", "email", "profile"],
        "groups_claim": "groups",
    }
    assert body["saml"] is None
    assert body["has_client_secret"] is True
    assert body["has_sp_key"] is False
    assert body["jit_enabled"] is True
    assert body["link_by_verified_email"] is False
    assert body["default_role"] is None
    row = await provider_row(app, "keycloak")
    assert stored_secret(app, row) == {"client_secret": SECRET}
    [entry] = await audit_entries(app, "sso_provider.create")
    assert entry["resource_type"] == "sso_provider"
    assert entry["resource_key"] == "keycloak"
    assert SECRET not in str(entry)


async def test_provider_can_be_read_and_listed(api: httpx.AsyncClient) -> None:
    created = await create_provider(api, OIDC)
    await create_provider(api, SAML)

    r = await api.get("/api/sso/providers/keycloak")
    assert r.status_code == 200, r.text
    assert r.json() == created
    r = await api.get("/api/sso/providers", params={"limit": 1})
    assert r.status_code == 200, r.text
    page = r.json()
    assert [p["key"] for p in page["items"]] == ["kc-saml"]
    r = await api.get("/api/sso/providers", params={"cursor": page["next_cursor"]})
    assert [p["key"] for p in r.json()["items"]] == ["keycloak"]
    assert_error(await api.get("/api/sso/providers/nope"), 404, "not_found")


@pytest.mark.parametrize(
    "change",
    [
        {"client_secret": None},  # OIDC needs a secret
        {"protocol": "saml"},  # the oidc block does not match
        {"saml": {"idp_metadata_xml": METADATA}},  # both blocks
        {"oidc": None},  # no block
        {"oidc": {"issuer": "not a url", "client_id": "x"}},
        {"sp_private_key": SP_KEY, "sp_certificate": SP_CERT},  # SAML-only fields
    ],
)
async def test_invalid_oidc_provider_is_refused(
    api: httpx.AsyncClient, change: dict[str, Any]
) -> None:
    body = {k: v for k, v in (OIDC | change).items() if v is not None}

    r = await api.post("/api/sso/providers", json=body)

    assert r.status_code == 422, r.text


async def test_duplicate_key_is_a_conflict(api: httpx.AsyncClient) -> None:
    await create_provider(api, OIDC)

    r = await api.post("/api/sso/providers", json=OIDC)

    assert_error(r, 409, "key_conflict")


async def test_patch_keeps_the_secret_unless_one_is_given(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_provider(api, OIDC)

    r = await api.patch(
        "/api/sso/providers/keycloak", json={"name": "Corporate SSO", "enabled": False}
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Corporate SSO"
    assert r.json()["enabled"] is False
    assert stored_secret(app, await provider_row(app, "keycloak")) == {"client_secret": SECRET}

    r = await api.patch("/api/sso/providers/keycloak", json={"client_secret": "rotated-secret"})
    assert r.status_code == 200, r.text
    assert r.json()["has_client_secret"] is True
    assert stored_secret(app, await provider_row(app, "keycloak")) == {
        "client_secret": "rotated-secret"
    }
    entries = await audit_entries(app, "sso_provider.update")
    assert [e["after"]["name"] for e in entries] == ["Corporate SSO", "Corporate SSO"]
    assert entries[0]["before"]["name"] == "Keycloak"
    assert "rotated-secret" not in str(entries)


async def test_patch_replaces_config_and_default_role(api: httpx.AsyncClient) -> None:
    await create_provider(api, OIDC | {"default_role": "viewer"})

    r = await api.patch(
        "/api/sso/providers/keycloak",
        json={
            "oidc": {
                "issuer": "https://other.example.test",
                "client_id": "mf",
                "groups_claim": "roles",
            },
            "jit_enabled": False,
            "link_by_verified_email": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Kept verbatim: it must match the ID token's `iss` exactly.
    assert body["oidc"]["issuer"] == "https://other.example.test"
    assert body["oidc"]["groups_claim"] == "roles"
    assert body["jit_enabled"] is False
    assert body["link_by_verified_email"] is True
    assert body["default_role"] == "viewer"

    r = await api.patch("/api/sso/providers/keycloak", json={"default_role": None})
    assert r.status_code == 200, r.text
    assert r.json()["default_role"] is None


async def test_patch_refuses_a_block_for_the_other_protocol(api: httpx.AsyncClient) -> None:
    await create_provider(api, OIDC)

    r = await api.patch(
        "/api/sso/providers/keycloak", json={"saml": {"idp_metadata_xml": METADATA}}
    )

    assert_error(r, 422, "validation_error")
    r = await api.patch("/api/sso/providers/keycloak", json={"sp_private_key": SP_KEY})
    assert_error(r, 422, "validation_error")


async def test_unknown_default_role_is_not_found(api: httpx.AsyncClient) -> None:
    r = await api.post("/api/sso/providers", json=OIDC | {"default_role": "nope"})

    assert_error(r, 404, "not_found")


async def test_saml_provider_with_metadata_xml(app: FastAPI, api: httpx.AsyncClient) -> None:
    body = await create_provider(api, SAML | {"sp_private_key": SP_KEY, "sp_certificate": SP_CERT})

    assert body["saml"] == {
        "idp_metadata_xml": METADATA,
        "idp_metadata_url": None,
        "sp_entity_id": None,
        "email_attribute": "email",
        "name_attribute": "name",
        "groups_attribute": "groups",
    }
    assert body["oidc"] is None
    assert body["has_client_secret"] is False
    assert body["has_sp_key"] is True
    assert stored_secret(app, await provider_row(app, "kc-saml")) == {
        "sp_private_key": SP_KEY,
        "sp_certificate": SP_CERT,
    }
    [entry] = await audit_entries(app, "sso_provider.create")
    assert "sp-private-key-material" not in str(entry)


async def test_saml_provider_without_sp_key_stores_no_secret(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    body = await create_provider(api, SAML)

    assert body["has_sp_key"] is False
    assert (await provider_row(app, "kc-saml")).secret_encrypted is None


@pytest.mark.parametrize(
    "saml",
    [
        {},  # no metadata source
        {"idp_metadata_xml": METADATA, "idp_metadata_url": METADATA_URL},  # both
    ],
)
async def test_saml_needs_exactly_one_metadata_source(
    api: httpx.AsyncClient, saml: dict[str, Any]
) -> None:
    r = await api.post("/api/sso/providers", json=SAML | {"saml": saml})

    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "change",
    [
        {"sp_private_key": SP_KEY},  # without its certificate
        {"client_secret": SECRET},  # OIDC-only
        {"oidc": OIDC["oidc"]},
    ],
)
async def test_invalid_saml_provider_is_refused(
    api: httpx.AsyncClient, change: dict[str, Any]
) -> None:
    r = await api.post("/api/sso/providers", json=SAML | change)

    assert r.status_code == 422, r.text


async def test_saml_metadata_url_is_fetched_and_stored(
    app: FastAPI, api: httpx.AsyncClient, metadata_requests: list[httpx.Request]
) -> None:
    body = await create_provider(api, SAML | {"saml": {"idp_metadata_url": METADATA_URL}})

    assert [str(r.url) for r in metadata_requests] == [METADATA_URL]
    assert body["saml"]["idp_metadata_url"] == METADATA_URL
    assert body["saml"]["idp_metadata_xml"] == METADATA
    row = await provider_row(app, "kc-saml")
    assert row.config["idp_metadata_xml"] == METADATA


async def test_unreachable_metadata_url_is_refused(
    api: httpx.AsyncClient, metadata_requests: list[httpx.Request]
) -> None:
    url = "https://idp.example.test/missing"

    r = await api.post("/api/sso/providers", json=SAML | {"saml": {"idp_metadata_url": url}})

    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "saml.idp_metadata_url"}
    assert [str(r.url) for r in metadata_requests] == [url]


async def test_delete_provider(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_provider(api, OIDC)
    r = await api.post(
        "/api/sso/providers/keycloak/mappings",
        json={"group": "admins", "role": "admin", "scope": "org"},
    )
    assert r.status_code == 201, r.text

    r = await api.delete("/api/sso/providers/keycloak")

    assert r.status_code == 204, r.text
    assert_error(await api.get("/api/sso/providers/keycloak"), 404, "not_found")
    async with app.state.sessionmaker() as session:
        assert list(await session.scalars(select(SsoGroupMapping))) == []
    [entry] = await audit_entries(app, "sso_provider.delete")
    assert entry["before"]["key"] == "keycloak"
    assert SECRET not in str(entry)


async def test_mappings_can_be_created_listed_and_deleted(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_provider(api, OIDC)
    await create_project(api)
    r = await api.post("/api/teams", json={"key": "platform", "name": "Platform"})
    assert r.status_code == 201, r.text
    base = "/api/sso/providers/keycloak/mappings"

    team = await api.post(base, json={"group": "platform-devs", "team": "platform"})
    role = await api.post(
        base,
        json={
            "group": "prod-ops",
            "role": "editor",
            "scope": "environment",
            "project": "demo",
            "env": "production",
        },
    )

    assert team.status_code == 201, team.text
    assert role.status_code == 201, role.text
    assert team.json() | {"id": None} == {
        "id": None,
        "group": "platform-devs",
        "team": "platform",
        "role": None,
        "scope": None,
        "project": None,
        "env": None,
    }
    assert role.json() | {"id": None} == {
        "id": None,
        "group": "prod-ops",
        "team": None,
        "role": "editor",
        "scope": "environment",
        "project": "demo",
        "env": "production",
    }
    r = await api.get(base)
    assert r.status_code == 200, r.text
    assert sorted(m["id"] for m in r.json()["items"]) == sorted(
        [team.json()["id"], role.json()["id"]]
    )
    assert r.json()["next_cursor"] is None

    r = await api.delete(f"{base}/{team.json()['id']}")

    assert r.status_code == 204, r.text
    assert [m["id"] for m in (await api.get(base)).json()["items"]] == [role.json()["id"]]
    [created, _] = await audit_entries(app, "sso_mapping.create")
    assert created["resource_type"] == "sso_provider"
    assert created["resource_key"] == "keycloak"
    assert created["after"]["group"] == "platform-devs"
    [deleted] = await audit_entries(app, "sso_mapping.delete")
    assert deleted["before"]["team"] == "platform"
    assert_error(await api.delete(f"{base}/{team.json()['id']}"), 404, "not_found")


@pytest.mark.parametrize(
    "mapping",
    [
        {"group": "g"},  # no target
        {"group": "g", "team": "platform", "role": "viewer", "scope": "org"},  # both
        {"group": "g", "role": "viewer"},  # a role needs a scope
        {"group": "g", "team": "platform", "scope": "org"},  # a team takes no scope
        {"group": "g", "team": "platform", "project": "demo"},
        {"group": "g", "role": "viewer", "scope": "project"},  # project scope needs a project
        {"group": "g", "role": "viewer", "scope": "org", "project": "demo"},
        {"group": "", "team": "platform"},
    ],
)
async def test_invalid_mapping_is_refused(api: httpx.AsyncClient, mapping: dict[str, Any]) -> None:
    await create_provider(api, OIDC)

    r = await api.post("/api/sso/providers/keycloak/mappings", json=mapping)

    assert r.status_code == 422, r.text


async def test_mapping_to_unknown_targets_is_not_found(api: httpx.AsyncClient) -> None:
    await create_provider(api, OIDC)
    base = "/api/sso/providers/keycloak/mappings"

    assert_error(await api.post(base, json={"group": "g", "team": "nope"}), 404, "not_found")
    r = await api.post(base, json={"group": "g", "role": "nope", "scope": "org"})
    assert_error(r, 404, "not_found")
    r = await api.post(
        base, json={"group": "g", "role": "viewer", "scope": "project", "project": "x"}
    )
    assert_error(r, 404, "not_found")
    r = await api.get("/api/sso/providers/nope/mappings")
    assert_error(r, 404, "not_found")


async def test_role_used_by_a_mapping_cannot_be_deleted(api: httpx.AsyncClient) -> None:
    r = await api.post(
        "/api/roles", json={"key": "flag-ops", "name": "Flag ops", "permissions": ["flag.toggle"]}
    )
    assert r.status_code == 201, r.text
    await create_provider(api, OIDC)
    r = await api.post(
        "/api/sso/providers/keycloak/mappings",
        json={"group": "ops", "role": "flag-ops", "scope": "org"},
    )
    assert r.status_code == 201, r.text

    body = assert_error(await api.delete("/api/roles/flag-ops"), 409, "in_use")

    assert body["details"]["used_by"] == [
        {"kind": "sso_mapping", "provider": "keycloak", "group": "ops"}
    ]


async def test_default_role_of_a_provider_cannot_be_deleted(api: httpx.AsyncClient) -> None:
    r = await api.post(
        "/api/roles", json={"key": "sso-user", "name": "SSO user", "permissions": ["project.read"]}
    )
    assert r.status_code == 201, r.text
    await create_provider(api, OIDC | {"default_role": "sso-user"})

    body = assert_error(await api.delete("/api/roles/sso-user"), 409, "in_use")

    assert body["details"]["used_by"] == [{"kind": "sso_provider", "provider": "keycloak"}]


async def test_public_providers_need_a_public_url(
    app: FastAPI, api: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    await create_provider(api, OIDC)

    r = await anon.get("/api/auth/providers")

    assert r.status_code == 200, r.text
    assert r.json() == []


async def test_public_providers_lists_the_enabled_ones(
    app: FastAPI, api: httpx.AsyncClient, anon: httpx.AsyncClient
) -> None:
    app.state.settings = app.state.settings.model_copy(
        update={"public_url": AnyHttpUrl(PUBLIC_URL)}
    )
    await create_provider(api, OIDC)
    await create_provider(api, SAML)
    await create_provider(api, OIDC | {"key": "off", "name": "Disabled", "enabled": False})

    r = await anon.get("/api/auth/providers")

    assert r.status_code == 200, r.text
    assert r.json() == [
        {
            "key": "kc-saml",
            "name": "Keycloak SAML",
            "protocol": "saml",
            "start_url": "/api/auth/sso/kc-saml/start",
        },
        {
            "key": "keycloak",
            "name": "Keycloak",
            "protocol": "oidc",
            "start_url": "/api/auth/sso/keycloak/start",
        },
    ]


async def test_viewer_cannot_manage_providers(
    api: httpx.AsyncClient, viewer: httpx.AsyncClient
) -> None:
    await create_provider(api, OIDC)

    assert_error(await viewer.get("/api/sso/providers"), 403, "forbidden")
    assert_error(await viewer.get("/api/sso/providers/keycloak"), 403, "forbidden")
    assert_error(await viewer.post("/api/sso/providers", json=SAML), 403, "forbidden")
    r = await viewer.patch("/api/sso/providers/keycloak", json={"name": "x"})
    assert_error(r, 403, "forbidden")
    assert_error(await viewer.delete("/api/sso/providers/keycloak"), 403, "forbidden")
    r = await viewer.get("/api/sso/providers/keycloak/mappings")
    assert_error(r, 403, "forbidden")
    r = await viewer.post(
        "/api/sso/providers/keycloak/mappings", json={"group": "g", "role": "admin", "scope": "org"}
    )
    assert_error(r, 403, "forbidden")


async def test_anonymous_cannot_manage_providers(anon: httpx.AsyncClient) -> None:
    assert_error(await anon.get("/api/sso/providers"), 401, "unauthorized")

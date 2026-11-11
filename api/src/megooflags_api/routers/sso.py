"""SSO: provider and group mapping admin (`/sso/providers`) and the public sign-in flow
(`/auth/...`)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.crypto import SecretBoxDep
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.sso import (
    PublicProvider,
    SsoMappingIn,
    SsoMappingOut,
    SsoProviderCreate,
    SsoProviderOut,
    SsoProviderUpdate,
)
from megooflags_api.services import sso_providers
from megooflags_api.services.audit import AuditDep, snapshot
from megooflags_api.settings import Settings

admin_router = APIRouter(prefix="/sso/providers", tags=["sso"], responses=ERROR_RESPONSES)
# Public (PUBLIC_ROUTES): the sign-in page and the IdP redirects call these.
flow_router = APIRouter(prefix="/auth", tags=["auth"])

ManageOrg = Annotated[Authz, Depends(require(Permission.ORG_MANAGE))]


@admin_router.get("")
async def list_providers(
    session: SessionDep,
    authz: ManageOrg,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[SsoProviderOut]:
    items, next_cursor = await sso_providers.list_providers(session, cursor, limit)
    return Page[SsoProviderOut](items=items, next_cursor=next_cursor)


@admin_router.post("", status_code=201)
async def create_provider(
    session: SessionDep,
    authz: ManageOrg,
    audit: AuditDep,
    box: SecretBoxDep,
    request: Request,
    body: SsoProviderCreate,
) -> SsoProviderOut:
    """OIDC providers need `oidc` and `client_secret`; SAML providers need `saml`, with the IdP
    metadata as XML or as a URL that is fetched now. Secrets are stored encrypted and never
    returned."""
    assert box is not None, "the lifespan refuses to start without MEGOOFLAGS_SECRET_KEY"
    http = sso_providers.get_sso_http(request.app)
    provider = await sso_providers.create_provider(session, body, box, http)
    out = sso_providers.provider_out(provider, body.default_role)
    await audit.record("sso_provider.create", "sso_provider", out.key, after=snapshot(out))
    await session.commit()
    return out


@admin_router.get("/{key}")
async def read_provider(session: SessionDep, authz: ManageOrg, key: str) -> SsoProviderOut:
    provider = await sso_providers.get_by_key(session, key)
    return await sso_providers.read_provider(session, provider)


@admin_router.patch("/{key}")
async def update_provider(
    session: SessionDep,
    authz: ManageOrg,
    audit: AuditDep,
    box: SecretBoxDep,
    request: Request,
    key: str,
    body: SsoProviderUpdate,
) -> SsoProviderOut:
    assert box is not None, "the lifespan refuses to start without MEGOOFLAGS_SECRET_KEY"
    provider = await sso_providers.get_by_key(session, key)
    before = await sso_providers.read_provider(session, provider)
    http = sso_providers.get_sso_http(request.app)
    await sso_providers.update_provider(session, provider, body, box, http)
    out = await sso_providers.read_provider(session, provider)
    await audit.record(
        "sso_provider.update", "sso_provider", key, before=snapshot(before), after=snapshot(out)
    )
    await session.commit()
    return out


@admin_router.delete("/{key}", status_code=204)
async def delete_provider(
    session: SessionDep, authz: ManageOrg, audit: AuditDep, key: str
) -> Response:
    """Also deletes its group mappings and the links of users to it."""
    provider = await sso_providers.get_by_key(session, key)
    before = await sso_providers.read_provider(session, provider)
    await sso_providers.delete_provider(session, provider)
    await audit.record("sso_provider.delete", "sso_provider", key, before=snapshot(before))
    await session.commit()
    return Response(status_code=204)


@admin_router.get("/{key}/mappings")
async def list_mappings(
    session: SessionDep,
    authz: ManageOrg,
    key: str,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[SsoMappingOut]:
    provider = await sso_providers.get_by_key(session, key)
    items, next_cursor = await sso_providers.list_mappings(session, provider, cursor, limit)
    return Page[SsoMappingOut](items=items, next_cursor=next_cursor)


@admin_router.post("/{key}/mappings", status_code=201)
async def create_mapping(
    session: SessionDep, authz: ManageOrg, audit: AuditDep, key: str, body: SsoMappingIn
) -> SsoMappingOut:
    """On each sign-in, members of the IdP group join the team, or get the role at the scope."""
    provider = await sso_providers.get_by_key(session, key)
    mapping = await sso_providers.create_mapping(session, provider, body)
    out = await sso_providers.mapping_out(session, mapping.id)
    await audit.record(
        "sso_mapping.create",
        "sso_provider",
        key,
        project_id=mapping.project_id,
        environment_id=mapping.environment_id,
        after=snapshot(out),
    )
    await session.commit()
    return out


@admin_router.delete("/{key}/mappings/{id}", status_code=204)
async def delete_mapping(
    session: SessionDep, authz: ManageOrg, audit: AuditDep, key: str, id: uuid.UUID
) -> Response:
    provider = await sso_providers.get_by_key(session, key)
    mapping = await sso_providers.get_mapping(session, provider, id)
    before = await sso_providers.mapping_out(session, id)
    project_id, environment_id = mapping.project_id, mapping.environment_id
    await sso_providers.delete_mapping(session, mapping)
    await audit.record(
        "sso_mapping.delete",
        "sso_provider",
        key,
        project_id=project_id,
        environment_id=environment_id,
        before=snapshot(before),
    )
    await session.commit()
    return Response(status_code=204)


@flow_router.get("/providers")
async def list_public_providers(session: SessionDep, request: Request) -> list[PublicProvider]:
    """The enabled providers, for the sign-in page. Empty without MEGOOFLAGS_PUBLIC_URL, since
    SSO needs a stable callback URL."""
    settings: Settings = request.app.state.settings
    if settings.public_url is None:
        return []
    return await sso_providers.public_providers(session)

"""SSO providers and their IdP group mappings.

Client secrets and SAML SP keys are stored encrypted with the SecretBox and are never returned.
A SAML provider configured with a metadata URL keeps the XML fetched when it was saved.
"""

import json
import uuid
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import Row, Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.crypto import SecretBox
from megooflags_api.errors import key_conflict, not_found, validation_error
from megooflags_api.models import Environment, Project, Role, SsoGroupMapping, SsoProvider, Team
from megooflags_api.models.enums import SsoProtocol
from megooflags_api.schemas.identity import ScopeFields
from megooflags_api.schemas.sso import (
    OidcConfig,
    PublicProvider,
    SamlConfig,
    SamlConfigOut,
    SsoMappingIn,
    SsoMappingOut,
    SsoProviderCreate,
    SsoProviderOut,
    SsoProviderUpdate,
)
from megooflags_api.services import roles, teams
from megooflags_api.services.bindings import resolve_scope
from megooflags_api.services.pagination import decode_cursor, encode_cursor, page_slice


def get_sso_http(app: FastAPI) -> httpx.AsyncClient:
    """The HTTP client for calls to identity providers, created on first use; the lifespan
    closes it."""
    if app.state.sso_http is None:
        app.state.sso_http = httpx.AsyncClient(timeout=10)
    client: httpx.AsyncClient = app.state.sso_http
    return client


def provider_out(provider: SsoProvider, default_role: str | None) -> SsoProviderOut:
    oidc = provider.protocol == SsoProtocol.OIDC
    has_secret = provider.secret_encrypted is not None
    return SsoProviderOut(
        id=provider.id,
        key=provider.key,
        name=provider.name,
        protocol=provider.protocol,
        enabled=provider.enabled,
        oidc=OidcConfig.model_validate(provider.config) if oidc else None,
        saml=None if oidc else SamlConfigOut.model_validate(provider.config),
        has_client_secret=oidc and has_secret,
        has_sp_key=not oidc and has_secret,
        jit_enabled=provider.jit_enabled,
        link_by_verified_email=provider.link_by_verified_email,
        default_role=default_role,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _out_query() -> Select[SsoProvider, str | None]:
    return select(SsoProvider, Role.key).outerjoin(Role, Role.id == SsoProvider.default_role_id)


async def list_providers(
    session: AsyncSession, cursor: str | None, limit: int
) -> tuple[list[SsoProviderOut], str | None]:
    query = _out_query().order_by(SsoProvider.key).limit(limit + 1)
    if cursor is not None:
        query = query.where(SsoProvider.key > decode_cursor(cursor))
    rows = list((await session.execute(query)).all())
    page, next_cursor = page_slice(rows, limit, lambda row: row[0].key)
    return [provider_out(provider, role) for provider, role in page], next_cursor


async def get_by_key(session: AsyncSession, key: str) -> SsoProvider:
    provider = await session.scalar(select(SsoProvider).where(SsoProvider.key == key))
    if provider is None:
        raise not_found("SSO provider", key)
    return provider


async def read_provider(session: AsyncSession, provider: SsoProvider) -> SsoProviderOut:
    role = None
    if provider.default_role_id is not None:
        role = await session.scalar(select(Role.key).where(Role.id == provider.default_role_id))
    return provider_out(provider, role)


async def public_providers(session: AsyncSession) -> list[PublicProvider]:
    rows = await session.execute(
        select(SsoProvider.key, SsoProvider.name, SsoProvider.protocol)
        .where(SsoProvider.enabled)
        .order_by(SsoProvider.key)
    )
    return [
        PublicProvider(
            key=key, name=name, protocol=protocol, start_url=f"/api/auth/sso/{key}/start"
        )
        for key, name, protocol in rows
    ]


async def _saml_config(http: httpx.AsyncClient, saml: SamlConfig) -> dict[str, Any]:
    """The config to store; a metadata URL is fetched now (422 when that fails)."""
    config = saml.model_dump(mode="json")
    if saml.idp_metadata_url is not None:
        try:
            response = await http.get(saml.idp_metadata_url)
            response.raise_for_status()
        except httpx.HTTPError:
            raise validation_error(
                f"cannot fetch the IdP metadata from {saml.idp_metadata_url}",
                "saml.idp_metadata_url",
            ) from None
        config["idp_metadata_xml"] = response.text
    return config


def _encrypt(box: SecretBox, secret: dict[str, str]) -> str:
    return box.encrypt(json.dumps(secret))


async def _role_id(session: AsyncSession, key: str | None) -> uuid.UUID | None:
    return None if key is None else (await roles.get_by_key(session, key)).id


async def _flush_provider(session: AsyncSession, key: str) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("SSO provider", key) from None
        raise


async def create_provider(
    session: AsyncSession, body: SsoProviderCreate, box: SecretBox, http: httpx.AsyncClient
) -> SsoProvider:
    """404 for an unknown default role; 409 for a duplicate key."""
    secret: dict[str, str] | None = None
    if body.oidc is not None:
        config = body.oidc.model_dump(mode="json")
        assert body.client_secret is not None  # checked by SsoProviderCreate
        secret = {"client_secret": body.client_secret}
    else:
        assert body.saml is not None  # checked by SsoProviderCreate
        config = await _saml_config(http, body.saml)
        if body.sp_private_key is not None and body.sp_certificate is not None:
            secret = {"sp_private_key": body.sp_private_key, "sp_certificate": body.sp_certificate}
    provider = SsoProvider(
        key=body.key,
        name=body.name,
        protocol=body.protocol,
        enabled=body.enabled,
        config=config,
        secret_encrypted=None if secret is None else _encrypt(box, secret),
        jit_enabled=body.jit_enabled,
        link_by_verified_email=body.link_by_verified_email,
        default_role_id=await _role_id(session, body.default_role),
    )
    session.add(provider)
    await _flush_provider(session, body.key)
    return provider


def _check_update_protocol(provider: SsoProvider, body: SsoProviderUpdate) -> None:
    """422 for a block or secret of the other protocol."""
    foreign: tuple[str, ...]
    if provider.protocol == SsoProtocol.OIDC:
        foreign = ("saml", "sp_private_key", "sp_certificate")
    else:
        foreign = ("oidc", "client_secret")
    for field in foreign:
        if getattr(body, field) is not None:
            raise validation_error(f"'{field}' does not apply to a {provider.protocol} provider")
    if (body.sp_private_key is None) != (body.sp_certificate is None):
        raise validation_error("give both 'sp_private_key' and 'sp_certificate', or neither")


async def update_provider(
    session: AsyncSession,
    provider: SsoProvider,
    body: SsoProviderUpdate,
    box: SecretBox,
    http: httpx.AsyncClient,
) -> None:
    """Apply the change and flush; secrets are replaced only when given."""
    _check_update_protocol(provider, body)
    if body.name is not None:
        provider.name = body.name
    if body.enabled is not None:
        provider.enabled = body.enabled
    if body.jit_enabled is not None:
        provider.jit_enabled = body.jit_enabled
    if body.link_by_verified_email is not None:
        provider.link_by_verified_email = body.link_by_verified_email
    if "default_role" in body.model_fields_set:
        provider.default_role_id = await _role_id(session, body.default_role)
    if body.oidc is not None:
        provider.config = body.oidc.model_dump(mode="json")
    if body.saml is not None:
        provider.config = await _saml_config(http, body.saml)
    if body.client_secret is not None:
        provider.secret_encrypted = _encrypt(box, {"client_secret": body.client_secret})
    if body.sp_private_key is not None and body.sp_certificate is not None:
        provider.secret_encrypted = _encrypt(
            box, {"sp_private_key": body.sp_private_key, "sp_certificate": body.sp_certificate}
        )
    await session.flush()


async def delete_provider(session: AsyncSession, provider: SsoProvider) -> None:
    """Its mappings and linked identities go with it."""
    await session.delete(provider)
    await session.flush()


def _mapping_query() -> Select[SsoGroupMapping, str | None, str | None, str | None, str | None]:
    return (
        select(SsoGroupMapping, Team.key, Role.key, Project.key, Environment.key)
        .outerjoin(Team, Team.id == SsoGroupMapping.team_id)
        .outerjoin(Role, Role.id == SsoGroupMapping.role_id)
        .outerjoin(Project, Project.id == SsoGroupMapping.project_id)
        .outerjoin(Environment, Environment.id == SsoGroupMapping.environment_id)
    )


def _mapping_out(
    row: Row[SsoGroupMapping, str | None, str | None, str | None, str | None],
) -> SsoMappingOut:
    mapping, team, role, project, env = row
    return SsoMappingOut(
        id=mapping.id,
        group=mapping.idp_group,
        team=team,
        role=role,
        scope=mapping.scope,
        project=project,
        env=env,
    )


async def mapping_out(session: AsyncSession, mapping_id: uuid.UUID) -> SsoMappingOut:
    row = (await session.execute(_mapping_query().where(SsoGroupMapping.id == mapping_id))).one()
    return _mapping_out(row)


async def list_mappings(
    session: AsyncSession, provider: SsoProvider, cursor: str | None, limit: int
) -> tuple[list[SsoMappingOut], str | None]:
    query = (
        _mapping_query()
        .where(SsoGroupMapping.provider_id == provider.id)
        .order_by(SsoGroupMapping.id)
        .limit(limit + 1)
    )
    if cursor is not None:
        try:
            after = uuid.UUID(decode_cursor(cursor))
        except ValueError:
            raise validation_error("invalid cursor", "cursor") from None
        query = query.where(SsoGroupMapping.id > after)
    rows = (await session.execute(query)).all()
    items = [_mapping_out(row) for row in rows[:limit]]
    next_cursor = encode_cursor(str(items[-1].id)) if len(rows) > limit else None
    return items, next_cursor


async def create_mapping(
    session: AsyncSession, provider: SsoProvider, body: SsoMappingIn
) -> SsoGroupMapping:
    """404 for an unknown team, role, project or environment."""
    mapping = SsoGroupMapping(provider_id=provider.id, idp_group=body.group)
    if body.team is not None:
        mapping.team_id = (await teams.get_by_key(session, body.team)).id
    else:
        assert body.role is not None and body.scope is not None  # checked by SsoMappingIn
        mapping.role_id = (await roles.get_by_key(session, body.role)).id
        mapping.scope = body.scope
        mapping.project_id, mapping.environment_id = await resolve_scope(
            session, ScopeFields(scope=body.scope, project=body.project, env=body.env)
        )
    session.add(mapping)
    await session.flush()
    return mapping


async def get_mapping(
    session: AsyncSession, provider: SsoProvider, mapping_id: uuid.UUID
) -> SsoGroupMapping:
    mapping = await session.scalar(
        select(SsoGroupMapping).where(
            SsoGroupMapping.id == mapping_id, SsoGroupMapping.provider_id == provider.id
        )
    )
    if mapping is None:
        raise not_found("SSO mapping", str(mapping_id))
    return mapping


async def delete_mapping(session: AsyncSession, mapping: SsoGroupMapping) -> None:
    await session.delete(mapping)
    await session.flush()

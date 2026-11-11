"""SSO: provider and group mapping admin (`/sso/providers`) and the public sign-in flow
(`/auth/...`)."""

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth import sessions
from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.auth.sso import SsoDenied, oidc, provisioning
from megooflags_api.auth.sso.state import STATE_SECONDS, pop_state, put_state
from megooflags_api.crypto import SecretBox, SecretBoxDep
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES, ErrorBody, not_found, public_url_required
from megooflags_api.models import SsoProvider
from megooflags_api.models.enums import SsoProtocol
from megooflags_api.routers.auth import session_principal, set_session_cookie, start_session
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.sso import (
    OidcConfig,
    PublicProvider,
    SsoMappingIn,
    SsoMappingOut,
    SsoProviderCreate,
    SsoProviderOut,
    SsoProviderUpdate,
)
from megooflags_api.services import sso_providers
from megooflags_api.services.audit import AuditDep, record, request_meta, snapshot
from megooflags_api.settings import Settings
from megooflags_api.urls import public_base_url

logger = logging.getLogger(__name__)

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


# Binds the login state to the browser that started it; the IdP redirect back is a top-level
# GET, so SameSite=Lax still sends it.
SSO_COOKIE = "mf_sso"
SSO_COOKIE_PATH = "/api/auth/sso"
FLOW_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorBody},
    409: {"model": ErrorBody},
    503: {"model": ErrorBody},
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def safe_next(value: str | None) -> str:
    """`value` when it is a path on this site, otherwise "/"; never an open redirect."""
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        return "/"
    parts = urlsplit(value)
    return "/" if parts.scheme or parts.netloc else value


def _base_url(request: Request) -> str:
    """The public base URL; 409 public_url_required without MEGOOFLAGS_PUBLIC_URL."""
    settings: Settings = request.app.state.settings
    if settings.public_url is None:
        raise public_url_required()
    return public_base_url(request)


def _callback_url(request: Request, key: str) -> str:
    return f"{_base_url(request)}/api/auth/sso/{key}/callback"


async def _enabled_provider(session: AsyncSession, key: str) -> SsoProvider | None:
    provider: SsoProvider | None = await session.scalar(
        select(SsoProvider).where(SsoProvider.key == key, SsoProvider.enabled)
    )
    return provider


def _oidc_cache(request: Request) -> oidc.OidcCache:
    cache: oidc.OidcCache = request.app.state.oidc_cache
    return cache


def _clear_sso_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        SSO_COOKIE,
        path=SSO_COOKIE_PATH,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )


async def _deny(
    session: AsyncSession, request: Request, key: str, denied: SsoDenied, label: str | None
) -> RedirectResponse:
    """Undo the attempt, audit `auth.sso.denied` and send the browser to the sign-in page."""
    logger.warning("SSO sign-in through %r refused (%s): %s", key, denied.code, denied.reason)
    await session.rollback()
    await record(
        session,
        None,
        request_meta(request),
        "auth.sso.denied",
        "sso_provider",
        key,
        after={"error": denied.code, "reason": denied.reason},
        actor_label=label,
    )
    await session.commit()
    response = RedirectResponse(f"/sign-in?error={denied.code}", status_code=303)
    _clear_sso_cookie(response, request)
    return response


@flow_router.get(
    "/sso/{provider}/start",
    status_code=302,
    response_class=RedirectResponse,
    responses=FLOW_RESPONSES,
)
async def sso_start(
    session: SessionDep, request: Request, provider: str, next: str | None = None
) -> RedirectResponse:
    """Sends the browser to the identity provider. After signing in it comes back to `next` (a
    path on this site; anything else means "/"), or to `/sign-in?error=<code>`."""
    redirect_uri = _callback_url(request, provider)
    row = await _enabled_provider(session, provider)
    # SAML providers start here too once SAML sign-in exists.
    if row is None or row.protocol != SsoProtocol.OIDC:
        raise not_found("SSO provider", provider)
    http = sso_providers.get_sso_http(request.app)
    config = OidcConfig.model_validate(row.config)
    try:
        auth = await oidc.authorization_request(http, _oidc_cache(request), config, redirect_uri)
    except SsoDenied as denied:
        return await _deny(session, request, provider, denied, None)
    browser = secrets.token_urlsafe(32)
    await put_state(
        request.app.state.valkey,
        "oidc",
        auth.state,
        {
            "provider": row.key,
            "nonce": auth.nonce,
            "code_verifier": auth.code_verifier,
            "next": safe_next(next),
            "browser": _hash(browser),
        },
    )
    response = RedirectResponse(auth.url, status_code=302)
    response.set_cookie(
        SSO_COOKIE,
        browser,
        max_age=STATE_SECONDS,
        path=SSO_COOKIE_PATH,
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="lax",
    )
    return response


def _client_secret(box: SecretBox, provider: SsoProvider) -> str:
    if provider.secret_encrypted is None:
        raise SsoDenied("sso_failed", "the provider has no client secret")
    try:
        secret: str = json.loads(box.decrypt(provider.secret_encrypted))["client_secret"]
    except (ValueError, KeyError):
        raise SsoDenied("sso_failed", "cannot decrypt the client secret") from None
    return secret


@flow_router.get(
    "/sso/{provider}/callback",
    status_code=303,
    response_class=RedirectResponse,
    responses=FLOW_RESPONSES,
)
async def sso_callback(
    session: SessionDep,
    request: Request,
    box: SecretBoxDep,
    provider: str,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Where the identity provider sends the browser back. Signs in, provisioning the user and
    syncing their groups, then redirects to `next`; on any failure, to `/sign-in?error=<code>`."""
    assert box is not None, "the lifespan refuses to start without MEGOOFLAGS_SECRET_KEY"
    redirect_uri = _callback_url(request, provider)
    label: str | None = None
    try:
        stored = None if state is None else await pop_state(request.app.state.valkey, "oidc", state)
        if stored is None or stored["provider"] != provider:
            raise SsoDenied("sso_failed", "unknown, used or expired state")
        cookie = request.cookies.get(SSO_COOKIE)
        if cookie is None or not hmac.compare_digest(_hash(cookie), stored["browser"]):
            raise SsoDenied("sso_failed", "the state belongs to another browser")
        if error is not None:
            raise SsoDenied("sso_failed", f"the identity provider answered {error[:200]!r}")
        if code is None:
            raise SsoDenied("sso_failed", "the callback has no code")
        row = await _enabled_provider(session, provider)
        if row is None or row.protocol != SsoProtocol.OIDC:
            raise SsoDenied("sso_failed", "the provider is gone or disabled")
        assertion = await oidc.complete(
            sso_providers.get_sso_http(request.app),
            _oidc_cache(request),
            row.key,
            OidcConfig.model_validate(row.config),
            _client_secret(box, row),
            code=code,
            redirect_uri=redirect_uri,
            code_verifier=stored["code_verifier"],
            nonce=stored["nonce"],
        )
        label = assertion.email or assertion.subject
        user = await provisioning.resolve_user(session, row, assertion)
    except SsoDenied as denied:
        return await _deny(session, request, provider, denied, label)

    incoming = sessions.read_cookie(request)
    if incoming is not None:
        await sessions.end_session_by_token(session, incoming)
    # SSO users rely on their identity provider's MFA: the session is active at once.
    session_row, token = await start_session(
        session, request, user, auth_method=provisioning.source_of(row)
    )
    principal = session_principal(user, session_row)
    meta = request_meta(request)
    user.last_login_at = datetime.now(UTC)
    await provisioning.sync_groups(session, row, user, assertion.groups, principal, meta)
    await record(session, principal, meta, "auth.sso.sign_in", "user", user.email)
    await session.commit()
    response = RedirectResponse(stored["next"], status_code=303)
    set_session_cookie(response, request, token)
    _clear_sso_cookie(response, request)
    return response

"""Who is calling: a signed-in user (session cookie) or an API token (`Authorization: Bearer`)."""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth import csrf, sessions
from megooflags_api.db import SessionDep
from megooflags_api.errors import mfa_enrolment_required, mfa_required, unauthorized
from megooflags_api.models import User
from megooflags_api.models.enums import ApiTokenKind, SessionState, UserStatus


class PrincipalKind(StrEnum):
    USER = "user"
    SERVICE_TOKEN = "service_token"
    PERSONAL_TOKEN = "personal_token"


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    id: uuid.UUID  # the user, or the token
    label: str
    auth_method: str  # the session's method ("password", "sso:<key>", ...), or "token"
    user_id: uuid.UUID | None = None  # the user, or a personal token's owner
    session_id: uuid.UUID | None = None
    session_state: SessionState | None = None
    # Personal tokens only: the token's permissions and scope.
    token_permissions: frozenset[str] | None = None
    token_project_id: uuid.UUID | None = None
    token_environment_id: uuid.UUID | None = None


def check_session_state(
    principal: Principal, *, allow_enrolment: bool = False, allow_pending: bool = False
) -> None:
    """A pending-MFA session is 401 mfa_required, an enrol-MFA one 403, unless allowed."""
    if principal.session_state == SessionState.PENDING_MFA and not allow_pending:
        raise mfa_required()
    if principal.session_state == SessionState.ENROL_MFA and not allow_enrolment:
        raise mfa_enrolment_required()


async def _from_token(session: AsyncSession, token: str) -> Principal:
    # Imported here: services.api_tokens imports the schemas, which import auth.rbac, which
    # imports this module.
    from megooflags_api.services.api_tokens import resolve_token, touch_last_used

    row = await resolve_token(session, token)
    if row is None:
        raise unauthorized("invalid API token")
    if row.kind == ApiTokenKind.SERVICE:
        principal = Principal(
            kind=PrincipalKind.SERVICE_TOKEN, id=row.id, label=row.name, auth_method="token"
        )
    else:
        owner = await session.get(User, row.owner_user_id)
        if owner is None or owner.status != UserStatus.ACTIVE:
            raise unauthorized("invalid API token")
        principal = Principal(
            kind=PrincipalKind.PERSONAL_TOKEN,
            id=row.id,
            label=f"{owner.email} ({row.name})",
            auth_method="token",
            user_id=owner.id,
            token_permissions=frozenset(row.permissions or ()),
            token_project_id=row.project_id,
            token_environment_id=row.environment_id,
        )
    await touch_last_used(session, row)
    return principal


async def resolve_principal(
    request: Request,
    session: AsyncSession,
    *,
    allow_enrolment: bool = False,
    allow_pending: bool = False,
) -> Principal:
    """The caller, from a Bearer API token or else the session cookie; 401 without either.

    Cookie requests with unsafe methods must pass the CSRF Origin check. Sets
    `request.state.principal`.
    """
    authorization = request.headers.get("authorization")
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer":
            raise unauthorized("unsupported authorization scheme")
        principal = await _from_token(session, token.strip())
    else:
        cookie = sessions.read_cookie(request)
        found = None if cookie is None else await sessions.lookup_session(session, cookie)
        if found is None:
            raise unauthorized("sign in or send an API token")
        row, user = found
        if user.status != UserStatus.ACTIVE:
            raise unauthorized("sign in or send an API token")
        csrf.check_origin(request)
        principal = Principal(
            kind=PrincipalKind.USER,
            id=user.id,
            label=user.email,
            auth_method=row.auth_method,
            user_id=user.id,
            session_id=row.id,
            session_state=row.state,
        )
        check_session_state(principal, allow_enrolment=allow_enrolment, allow_pending=allow_pending)
        await sessions.touch(session, row, request.app.state.settings)
    request.state.principal = principal
    return principal


async def get_principal(request: Request, session: SessionDep) -> Principal:
    """The caller, with the default session-state rules; resolved once per request."""
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is None:
        return await resolve_principal(request, session)
    check_session_state(principal)
    return principal


PrincipalDep = Annotated[Principal, Depends(get_principal)]

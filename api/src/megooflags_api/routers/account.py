"""The signed-in user's own account (`/account/*`): password change and sessions.

Every route needs a signed-in session; API tokens get 403.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select

from megooflags_api.auth import sessions
from megooflags_api.auth.passwords import verify_password
from megooflags_api.auth.require import Authz, authenticated
from megooflags_api.db import SessionDep
from megooflags_api.errors import (
    ERROR_RESPONSES,
    invalid_credentials,
    no_password,
    not_found,
    unauthorized,
)
from megooflags_api.models import User, UserSession
from megooflags_api.models.enums import SessionState
from megooflags_api.routers.auth import set_session_cookie
from megooflags_api.schemas.auth import PasswordChange, SessionOut
from megooflags_api.services.audit import AuditDep
from megooflags_api.services.users import set_password

router = APIRouter(prefix="/account", tags=["account"], responses=ERROR_RESPONSES)

SignedIn = Annotated[Authz, Depends(authenticated(session_only=True))]


async def _current(session: SessionDep, authz: Authz) -> tuple[User, UserSession]:
    principal = authz.principal
    assert principal.user_id is not None and principal.session_id is not None
    user = await session.get(User, principal.user_id)
    row = await session.get(UserSession, principal.session_id)
    if user is None or row is None:
        # Ended by a concurrent request since the principal was resolved.
        raise unauthorized("the session has ended; sign in again")
    return user, row


@router.put("/password", status_code=204)
async def change_password(
    session: SessionDep,
    authz: SignedIn,
    audit: AuditDep,
    request: Request,
    response: Response,
    body: PasswordChange,
) -> None:
    """Change your password. Your other sessions end, and this one gets a new cookie."""
    user, row = await _current(session, authz)
    if user.password_hash is None:
        raise no_password()
    if not verify_password(user.password_hash, body.current_password):
        raise invalid_credentials()
    # Checks the password policy (422 weak_password) before hashing.
    await set_password(session, user, body.new_password)
    new_row, token = await sessions.rotate(
        session, request, row, state=SessionState.ACTIVE, settings=request.app.state.settings
    )
    await sessions.end_sessions(session, user.id, except_id=new_row.id)
    await audit.record("account.password.change", "user", user.email)
    await session.commit()
    set_session_cookie(response, request, token)


@router.get("/sessions")
async def list_sessions(session: SessionDep, authz: SignedIn) -> list[SessionOut]:
    """Your unexpired sessions, most recently used first."""
    now = datetime.now(UTC)
    rows = await session.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == authz.principal.user_id,
            UserSession.idle_expires_at > now,
            UserSession.absolute_expires_at > now,
        )
        .order_by(UserSession.last_seen_at.desc(), UserSession.id)
    )
    return [
        SessionOut(
            id=row.id,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            ip=row.ip,
            user_agent=row.user_agent,
            auth_method=row.auth_method,
            current=row.id == authz.principal.session_id,
        )
        for row in rows
    ]


# Declared before `/sessions/{id}`, which would otherwise try to parse "others" as an id.
@router.delete("/sessions/others", status_code=204)
async def revoke_other_sessions(session: SessionDep, authz: SignedIn, audit: AuditDep) -> None:
    """End every session of yours except this one."""
    ended = await session.scalars(
        delete(UserSession)
        .where(
            UserSession.user_id == authz.principal.user_id,
            UserSession.id != authz.principal.session_id,
        )
        .returning(UserSession.id)
    )
    for session_id in sorted(ended.all()):
        await audit.record("account.session.revoke", "session", str(session_id))
    await session.commit()


@router.delete("/sessions/{id}", status_code=204)
async def revoke_session(
    session: SessionDep,
    authz: SignedIn,
    audit: AuditDep,
    request: Request,
    response: Response,
    id: uuid.UUID,
) -> None:
    """End one of your sessions. Ending the current one signs you out."""
    ended = await session.scalar(
        delete(UserSession)
        .where(UserSession.id == id, UserSession.user_id == authz.principal.user_id)
        .returning(UserSession.id)
    )
    if ended is None:
        raise not_found("session", str(id))
    await audit.record("account.session.revoke", "session", str(id))
    await session.commit()
    if id == authz.principal.session_id:
        sessions.clear_cookie(response, request)

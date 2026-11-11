"""Server-side sessions and the session cookie.

The cookie carries a random token; the database keeps only its sha256. A session ends at its idle
expiry (extended by `touch`) or its absolute expiry, whichever comes first.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import User, UserSession
from megooflags_api.models.enums import SessionState
from megooflags_api.settings import Settings

COOKIE_SECURE = "__Host-mf_session"
COOKIE_PLAIN = "mf_session"
TOUCH_INTERVAL = timedelta(seconds=60)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _is_https(request: Request) -> bool:
    # Uvicorn has already applied X-Forwarded-Proto from a trusted proxy to the scheme.
    return request.url.scheme == "https"


def _cookie_name(request: Request) -> str:
    return COOKIE_SECURE if _is_https(request) else COOKIE_PLAIN


def read_cookie(request: Request) -> str | None:
    return request.cookies.get(_cookie_name(request))


def set_cookie(response: Response, request: Request, token: str, max_age: int) -> None:
    """`__Host-mf_session` (Secure) over https, `mf_session` over plain http (local dev)."""
    response.set_cookie(
        _cookie_name(request),
        token,
        max_age=max_age,
        path="/",
        secure=_is_https(request),
        httponly=True,
        samesite="lax",
    )


def clear_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        _cookie_name(request), path="/", secure=_is_https(request), httponly=True, samesite="lax"
    )


async def _add_session(
    session: AsyncSession,
    request: Request,
    user_id: uuid.UUID,
    *,
    state: SessionState,
    auth_method: str,
    settings: Settings,
    mfa_verified_at: datetime | None,
) -> tuple[UserSession, str]:
    now = datetime.now(UTC)
    if state == SessionState.PENDING_MFA:
        idle = absolute = now + timedelta(minutes=settings.pending_mfa_minutes)
    else:
        absolute = now + timedelta(days=settings.session_absolute_days)
        idle = min(now + timedelta(hours=settings.session_idle_hours), absolute)
    token = secrets.token_urlsafe(32)
    user_agent = request.headers.get("user-agent")
    row = UserSession(
        token_hash=_hash(token),
        user_id=user_id,
        state=state,
        auth_method=auth_method,
        created_at=now,
        last_seen_at=now,
        idle_expires_at=idle,
        absolute_expires_at=absolute,
        mfa_verified_at=mfa_verified_at,
        ip=request.client.host if request.client else None,
        user_agent=None if user_agent is None else user_agent[:512],
    )
    session.add(row)
    await session.flush()
    return row, token


async def create_session(
    session: AsyncSession,
    request: Request,
    user: User,
    *,
    state: SessionState,
    auth_method: str,
    settings: Settings,
    mfa_verified: bool = False,
) -> tuple[UserSession, str]:
    """Add a session and return it with its cookie token. Flushes; the caller commits."""
    return await _add_session(
        session,
        request,
        user.id,
        state=state,
        auth_method=auth_method,
        settings=settings,
        mfa_verified_at=datetime.now(UTC) if mfa_verified else None,
    )


async def lookup_session(session: AsyncSession, token: str) -> tuple[UserSession, User] | None:
    """The session for a cookie token and its user, or None.

    An expired session is deleted and committed here, so the cleanup survives the 401 that follows.
    """
    found = (
        await session.execute(
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.token_hash == _hash(token))
        )
    ).one_or_none()
    if found is None:
        return None
    row, user = found
    now = datetime.now(UTC)
    if now >= row.idle_expires_at or now >= row.absolute_expires_at:
        await session.execute(delete(UserSession).where(UserSession.id == row.id))
        await session.commit()
        return None
    return row, user


async def touch(session: AsyncSession, row: UserSession, settings: Settings) -> None:
    """Record activity and extend the idle expiry, capped at the absolute expiry.

    Writes at most every 60 s, and commits, because read-only requests never commit.
    """
    now = datetime.now(UTC)
    if now - row.last_seen_at < TOUCH_INTERVAL:
        return
    row.last_seen_at = now
    row.idle_expires_at = min(
        now + timedelta(hours=settings.session_idle_hours), row.absolute_expires_at
    )
    await session.commit()


async def rotate(
    session: AsyncSession,
    request: Request,
    row: UserSession,
    *,
    state: SessionState,
    settings: Settings,
    mfa_verified: bool = False,
    auth_method: str | None = None,
) -> tuple[UserSession, str]:
    """Replace a session with a new one (new token, new lifetime) for the same user, keeping its
    auth method unless `auth_method` is given.

    The old row is deleted. Flushes; the caller commits.
    """
    new_row, token = await _add_session(
        session,
        request,
        row.user_id,
        state=state,
        auth_method=row.auth_method if auth_method is None else auth_method,
        settings=settings,
        mfa_verified_at=datetime.now(UTC) if mfa_verified else row.mfa_verified_at,
    )
    await session.execute(delete(UserSession).where(UserSession.id == row.id))
    return new_row, token


async def end_session_by_token(session: AsyncSession, token: str) -> None:
    """Delete the session a cookie token belongs to, if any. The caller commits."""
    await session.execute(delete(UserSession).where(UserSession.token_hash == _hash(token)))


async def end_sessions(
    session: AsyncSession, user_id: uuid.UUID, *, except_id: uuid.UUID | None = None
) -> int:
    """Delete a user's sessions, except `except_id`; returns how many ended. The caller commits."""
    statement = delete(UserSession).where(UserSession.user_id == user_id)
    if except_id is not None:
        statement = statement.where(UserSession.id != except_id)
    result = await session.execute(statement)
    return result.rowcount  # type: ignore[attr-defined, no-any-return]

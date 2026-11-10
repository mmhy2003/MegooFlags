from datetime import UTC, datetime
from typing import NoReturn

from fastapi import APIRouter, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth import sessions
from megooflags_api.auth.mfa import (
    has_factor,
    sign_in_methods,
    use_recovery_code,
    user_factors,
    verify_totp,
)
from megooflags_api.auth.passwords import hash_password, needs_rehash, verify_password
from megooflags_api.auth.principal import Principal, PrincipalKind, resolve_principal
from megooflags_api.auth.throttle import AuthThrottle
from megooflags_api.crypto import SecretBoxDep
from megooflags_api.db import SessionDep
from megooflags_api.errors import (
    ErrorBody,
    forbidden,
    invalid_code,
    invalid_credentials,
    rate_limited,
)
from megooflags_api.models import TotpCredential, User, UserSession
from megooflags_api.models.enums import SessionState, UserStatus
from megooflags_api.schemas.auth import CodeIn, MeOut, SignInIn, SignInOut
from megooflags_api.services import org_settings
from megooflags_api.services.audit import record, request_meta
from megooflags_api.services.users import get_user_by_email

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={401: {"model": ErrorBody}, 403: {"model": ErrorBody}, 422: {"model": ErrorBody}},
)


def session_principal(user: User, row: UserSession) -> Principal:
    return Principal(
        kind=PrincipalKind.USER,
        id=user.id,
        label=user.email,
        auth_method=row.auth_method,
        user_id=user.id,
        session_id=row.id,
        session_state=row.state,
    )


async def start_session(
    session: AsyncSession, request: Request, user: User, *, auth_method: str
) -> tuple[UserSession, str]:
    """A new active session for `user`. Flushes; the caller audits, commits and sets the cookie."""
    return await sessions.create_session(
        session,
        request,
        user,
        state=SessionState.ACTIVE,
        auth_method=auth_method,
        settings=request.app.state.settings,
    )


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    max_age = int(request.app.state.settings.session_absolute_days * 86400)
    sessions.set_cookie(response, request, token, max_age)


@router.post("/sign-in", responses={429: {"model": ErrorBody}})
async def sign_in(
    session: SessionDep, request: Request, response: Response, body: SignInIn
) -> SignInOut:
    throttle: AuthThrottle = request.app.state.auth_throttle
    meta = request_meta(request)
    retry_after = await throttle.blocked(body.email, meta.ip)
    if retry_after is not None:
        raise rate_limited(retry_after)

    user = await get_user_by_email(session, body.email)
    # Verified even for unknown emails (against a dummy hash), so timing reveals nothing.
    password_ok = verify_password(None if user is None else user.password_hash, body.password)
    if user is None or not password_ok or user.status != UserStatus.ACTIVE:
        await throttle.failed(body.email, meta.ip)
        await record(
            session, None, meta, "auth.sign_in.failed", "user", body.email, actor_label=body.email
        )
        await session.commit()
        raise invalid_credentials()

    assert user.password_hash is not None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    incoming = sessions.read_cookie(request)
    if incoming is not None:
        await sessions.end_session_by_token(session, incoming)
    # A user with a factor proves it next (/auth/mfa/*); without one, the org may require one.
    factors = await user_factors(session, user.id)
    if has_factor(factors):
        state = SessionState.PENDING_MFA
    elif await org_settings.require_mfa(session):
        state = SessionState.ENROL_MFA
    else:
        state = SessionState.ACTIVE
    row, token = await sessions.create_session(
        session,
        request,
        user,
        state=state,
        auth_method="password",
        settings=request.app.state.settings,
    )
    if state != SessionState.PENDING_MFA:
        user.last_login_at = datetime.now(UTC)
        await record(
            session, session_principal(user, row), meta, "auth.sign_in", "user", user.email
        )
    await session.commit()
    await throttle.cleared(body.email)
    set_session_cookie(response, request, token)
    if state == SessionState.PENDING_MFA:
        return SignInOut(status="mfa_required", mfa_methods=sign_in_methods(factors))
    if state == SessionState.ENROL_MFA:
        return SignInOut(status="enrol_mfa")
    return SignInOut(status="signed_in")


async def _pending_sign_in(
    session: AsyncSession, request: Request
) -> tuple[Principal, UserSession, User]:
    """The session waiting for a second factor and its user; 429 while MFA is throttled."""
    principal = await resolve_principal(request, session, allow_pending=True)
    if principal.session_state != SessionState.PENDING_MFA:
        raise forbidden("no sign-in is waiting for a second factor")
    row = await session.get(UserSession, principal.session_id)
    user = await session.get(User, principal.user_id)
    assert row is not None and user is not None
    throttle: AuthThrottle = request.app.state.auth_throttle
    retry_after = await throttle.mfa_blocked(user.id)
    if retry_after is not None:
        raise rate_limited(retry_after)
    return principal, row, user


async def _mfa_failed(
    session: AsyncSession, request: Request, principal: Principal, method: str
) -> NoReturn:
    throttle: AuthThrottle = request.app.state.auth_throttle
    assert principal.user_id is not None
    await throttle.mfa_failed(principal.user_id)
    await record(
        session,
        principal,
        request_meta(request),
        "auth.mfa.failed",
        "user",
        principal.label,
        after={"method": method},
    )
    await session.commit()
    raise invalid_code()


async def _finish_sign_in(
    session: AsyncSession,
    request: Request,
    response: Response,
    row: UserSession,
    user: User,
    method: str,
) -> SignInOut:
    """Replace the pending session with an active, MFA-verified one and audit the sign-in."""
    auth_method = f"{row.auth_method}+{method}"
    user.last_login_at = datetime.now(UTC)
    new_row, token = await sessions.rotate(
        session,
        request,
        row,
        state=SessionState.ACTIVE,
        settings=request.app.state.settings,
        mfa_verified=True,
        auth_method=auth_method,
    )
    principal = session_principal(user, new_row)
    await record(session, principal, request_meta(request), "auth.sign_in", "user", user.email)
    await session.commit()
    set_session_cookie(response, request, token)
    return SignInOut(status="signed_in")


@router.post("/mfa/totp", responses={429: {"model": ErrorBody}})
async def mfa_totp(
    session: SessionDep, request: Request, response: Response, box: SecretBoxDep, body: CodeIn
) -> SignInOut:
    """Finish signing in with a code from the authenticator app; each code works once."""
    principal, row, user = await _pending_sign_in(session, request)
    assert box is not None, "the lifespan refuses to start without MEGOOFLAGS_SECRET_KEY"
    cred = await session.scalar(
        select(TotpCredential)
        .where(TotpCredential.user_id == user.id, TotpCredential.confirmed_at.is_not(None))
        .with_for_update()
    )
    step = None if cred is None else verify_totp(cred, body.code, box)
    if cred is None or step is None:
        await _mfa_failed(session, request, principal, "totp")
    cred.last_used_step = step
    return await _finish_sign_in(session, request, response, row, user, "totp")


@router.post("/mfa/recovery", responses={429: {"model": ErrorBody}})
async def mfa_recovery(
    session: SessionDep, request: Request, response: Response, body: CodeIn
) -> SignInOut:
    """Finish signing in with a recovery code; each code works once."""
    principal, row, user = await _pending_sign_in(session, request)
    if not await use_recovery_code(session, user.id, body.code):
        await _mfa_failed(session, request, principal, "recovery")
    return await _finish_sign_in(session, request, response, row, user, "recovery")


@router.post("/sign-out", status_code=204)
async def sign_out(session: SessionDep, request: Request) -> Response:
    principal = await resolve_principal(request, session, allow_pending=True, allow_enrolment=True)
    if principal.session_id is None:
        raise forbidden("only a signed-in session can sign out")
    await session.execute(delete(UserSession).where(UserSession.id == principal.session_id))
    await record(
        session, principal, request_meta(request), "auth.sign_out", "user", principal.label
    )
    await session.commit()
    response = Response(status_code=204)
    sessions.clear_cookie(response, request)
    return response


@router.get("/me")
async def me(session: SessionDep, request: Request) -> MeOut:
    principal = await resolve_principal(request, session, allow_pending=True, allow_enrolment=True)
    user = None if principal.user_id is None else await session.get(User, principal.user_id)
    return MeOut(
        kind=principal.kind,
        id=principal.id,
        label=principal.label,
        email=None if user is None else user.email,
        name=None if user is None else user.name,
        auth_method=principal.auth_method,
        session_state=principal.session_state,
        mfa=(
            await user_factors(session, principal.id)
            if principal.kind == PrincipalKind.USER
            else None
        ),
        require_mfa=await org_settings.require_mfa(session),
    )

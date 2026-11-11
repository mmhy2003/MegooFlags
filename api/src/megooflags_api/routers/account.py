"""The signed-in user's own account (`/account/*`): password change, sessions, TOTP, passkeys,
recovery codes and personal API tokens.

Every route needs a signed-in session; API tokens get 403. The TOTP and passkey routes also accept
a session that must enrol a factor before it can do anything else (the org's `require_mfa`).
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select

from megooflags_api.auth import mfa, sessions, webauthn
from megooflags_api.auth.passwords import verify_password
from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, authenticated
from megooflags_api.crypto import SecretBoxDep
from megooflags_api.db import SessionDep
from megooflags_api.errors import (
    ERROR_RESPONSES,
    exceeds_owner_rights,
    forbidden,
    invalid_code,
    invalid_credentials,
    mfa_required_by_org,
    no_password,
    not_found,
    reauth_required,
    totp_exists,
    unauthorized,
    validation_error,
)
from megooflags_api.models import (
    RecoveryCode,
    TotpCredential,
    User,
    UserSession,
    WebauthnCredential,
)
from megooflags_api.models.enums import SessionState
from megooflags_api.routers.auth import set_session_cookie
from megooflags_api.schemas.auth import (
    CodeIn,
    PasskeyOptionsOut,
    PasskeyOut,
    PasskeyRegister,
    PasskeyRegistered,
    PasskeyRename,
    PasswordChange,
    RecoveryCodes,
    SessionOut,
    TotpConfirmed,
    TotpEnrolment,
)
from megooflags_api.schemas.tokens import (
    PersonalTokenCreate,
    PersonalTokenCreated,
    PersonalTokenOut,
)
from megooflags_api.services import api_tokens, org_settings
from megooflags_api.services.audit import AuditDep, snapshot
from megooflags_api.services.lookup import get_environment, get_project
from megooflags_api.services.users import set_password

router = APIRouter(prefix="/account", tags=["account"], responses=ERROR_RESPONSES)

SignedIn = Annotated[Authz, Depends(authenticated(session_only=True))]
MayEnrol = Annotated[Authz, Depends(authenticated(allow_enrolment=True, session_only=True))]


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


async def _lock_user(session: SessionDep, user: User) -> None:
    """Serialises the user's factor removals, so two racing ones cannot each see the other's
    factor and both skip the last-factor checks. Taken before any factor row lock."""
    await session.execute(select(User.id).where(User.id == user.id).with_for_update())


async def _check_may_add_factor(session: SessionDep, user: User, row: UserSession) -> None:
    """Adding a factor when you already have one needs a factor verified in the last 10 minutes
    (403 reauth_required): enrolling sets that time, so a stolen session could otherwise add its
    own factor and then remove yours."""
    if mfa.has_factor(await mfa.user_factors(session, user.id)) and not mfa.recently_verified(row):
        raise reauth_required()


async def _locked_totp(session: SessionDep, user: User) -> TotpCredential | None:
    return await session.scalar(
        select(TotpCredential).where(TotpCredential.user_id == user.id).with_for_update()
    )


@router.post("/totp", status_code=201)
async def enrol_totp(
    session: SessionDep, authz: MayEnrol, audit: AuditDep, box: SecretBoxDep
) -> TotpEnrolment:
    """Start setting up an authenticator app: add the secret (or scan `otpauth_url` as a QR
    code), then confirm with a code. Starting again replaces an unconfirmed secret; a confirmed
    one must be removed first (409 totp_exists). With a passkey, you must have verified a factor
    in the last 10 minutes (403 reauth_required)."""
    assert box is not None, "the lifespan refuses to start without MEGOOFLAGS_SECRET_KEY"
    user, row = await _current(session, authz)
    cred = await _locked_totp(session, user)
    if cred is not None and cred.confirmed_at is not None:
        raise totp_exists()
    await _check_may_add_factor(session, user, row)
    secret = mfa.new_totp_secret()
    if cred is None:
        session.add(TotpCredential(user_id=user.id, secret_encrypted=box.encrypt(secret)))
    else:
        cred.secret_encrypted = box.encrypt(secret)
        cred.last_used_step = None
    await session.flush()
    await audit.record("account.totp.enrol", "user", user.email)
    await session.commit()
    return TotpEnrolment(secret=secret, otpauth_url=mfa.otpauth_url(secret, user.email))


@router.post("/totp/confirm")
async def confirm_totp(
    session: SessionDep,
    authz: MayEnrol,
    audit: AuditDep,
    box: SecretBoxDep,
    request: Request,
    response: Response,
    body: CodeIn,
) -> TotpConfirmed:
    """Finish setting up the authenticator app with a code from it. Returns ten recovery codes
    when you have none left. A session that had to enrol a factor becomes a full one, with a new
    cookie."""
    assert box is not None, "the lifespan refuses to start without MEGOOFLAGS_SECRET_KEY"
    user, row = await _current(session, authz)
    cred = await _locked_totp(session, user)
    if cred is None:
        raise not_found("TOTP enrolment", user.email)
    if cred.confirmed_at is not None:
        raise totp_exists()
    await _check_may_add_factor(session, user, row)
    step = mfa.verify_totp(cred, body.code, box)
    if step is None:
        raise invalid_code()
    now = datetime.now(UTC)
    cred.confirmed_at = now
    cred.last_used_step = step
    codes = None
    if (await mfa.user_factors(session, user.id)).recovery_codes_left == 0:
        codes = await mfa.replace_recovery_codes(session, user.id)
    token = None
    if row.state == SessionState.ENROL_MFA:
        _, token = await sessions.rotate(
            session,
            request,
            row,
            state=SessionState.ACTIVE,
            settings=request.app.state.settings,
            mfa_verified=True,
        )
    else:
        row.mfa_verified_at = now
    await audit.record("account.totp.confirm", "user", user.email)
    await session.commit()
    if token is not None:
        set_session_cookie(response, request, token)
    return TotpConfirmed(recovery_codes=codes)


@router.delete("/totp", status_code=204)
async def remove_totp(session: SessionDep, authz: MayEnrol, audit: AuditDep) -> None:
    """Remove the authenticator app. If it is your last factor, your recovery codes go too; you
    must have verified a factor in the last 10 minutes (403 reauth_required), and the org must
    not require MFA (409 mfa_required_by_org)."""
    user, row = await _current(session, authz)
    await _lock_user(session, user)
    cred = await _locked_totp(session, user)
    if cred is None:
        raise not_found("TOTP", user.email)
    no_passkeys = (await mfa.user_factors(session, user.id)).passkeys == 0
    if cred.confirmed_at is not None and no_passkeys:
        if await org_settings.require_mfa(session):
            raise mfa_required_by_org()
        if not mfa.recently_verified(row):
            raise reauth_required()
    await mfa.remove_totp(session, user.id, with_recovery_codes=no_passkeys)
    await audit.record("account.totp.remove", "user", user.email)
    await session.commit()


def _passkey_out(row: WebauthnCredential) -> PasskeyOut:
    return PasskeyOut(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        transports=row.transports,
    )


async def _passkeys(session: SessionDep, user_id: uuid.UUID) -> list[WebauthnCredential]:
    rows = await session.scalars(
        select(WebauthnCredential)
        .where(WebauthnCredential.user_id == user_id)
        .order_by(WebauthnCredential.created_at, WebauthnCredential.id)
    )
    return list(rows)


async def _own_passkey(session: SessionDep, user: User, id: uuid.UUID) -> WebauthnCredential:
    row = await session.scalar(
        select(WebauthnCredential)
        .where(WebauthnCredential.id == id, WebauthnCredential.user_id == user.id)
        .with_for_update()
    )
    if row is None:
        raise not_found("passkey", str(id))
    return row


@router.get("/passkeys")
async def list_passkeys(session: SessionDep, authz: MayEnrol) -> list[PasskeyOut]:
    """Your passkeys, oldest first."""
    assert authz.principal.user_id is not None
    return [_passkey_out(row) for row in await _passkeys(session, authz.principal.user_id)]


@router.post("/passkeys/options")
async def passkey_registration_options(
    session: SessionDep, authz: MayEnrol, request: Request
) -> PasskeyOptionsOut:
    """Start registering a passkey: pass `options` to `navigator.credentials.create()`, then
    send the result to `POST /account/passkeys` within 5 minutes. Needs MEGOOFLAGS_PUBLIC_URL
    (409 public_url_required). With a factor already, you must have verified one in the last 10
    minutes (403 reauth_required)."""
    party = webauthn.rp(request.app.state.settings)
    user, row = await _current(session, authz)
    await _check_may_add_factor(session, user, row)
    challenge, options = webauthn.registration_options(
        party, user, await _passkeys(session, user.id)
    )
    challenge_id = await webauthn.save_challenge(
        request.app.state.valkey,
        webauthn.Challenge(challenge=challenge, purpose="register", user_id=user.id),
    )
    return PasskeyOptionsOut(challenge_id=challenge_id, options=options)


@router.post("/passkeys", status_code=201)
async def register_passkey(
    session: SessionDep,
    authz: MayEnrol,
    audit: AuditDep,
    request: Request,
    response: Response,
    body: PasskeyRegister,
) -> PasskeyRegistered:
    """Finish registering a passkey with the browser's answer to the options. Returns ten
    recovery codes when you have none left. A session that had to enrol a factor becomes a full
    one, with a new cookie."""
    party = webauthn.rp(request.app.state.settings)
    user, row = await _current(session, authz)
    await _check_may_add_factor(session, user, row)
    challenge = await webauthn.take_challenge(request.app.state.valkey, body.challenge_id)
    if challenge is None or challenge.purpose != "register" or challenge.user_id != user.id:
        raise validation_error("the passkey challenge is unknown or expired; start again")
    verified = webauthn.verify_registration(party, challenge.challenge, body.credential)
    if verified is None:
        raise validation_error("the passkey could not be verified", "credential")
    passkey = WebauthnCredential(
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=webauthn.transports(body.credential),
        name=body.name,
    )
    session.add(passkey)
    await session.flush()
    codes = None
    if (await mfa.user_factors(session, user.id)).recovery_codes_left == 0:
        codes = await mfa.replace_recovery_codes(session, user.id)
    token = None
    if row.state == SessionState.ENROL_MFA:
        _, token = await sessions.rotate(
            session,
            request,
            row,
            state=SessionState.ACTIVE,
            settings=request.app.state.settings,
            mfa_verified=True,
        )
    else:
        row.mfa_verified_at = datetime.now(UTC)
    out = _passkey_out(passkey)
    await audit.record("account.passkey.register", "passkey", str(passkey.id), after=snapshot(out))
    await session.commit()
    if token is not None:
        set_session_cookie(response, request, token)
    return PasskeyRegistered(passkey=out, recovery_codes=codes)


@router.patch("/passkeys/{id}")
async def rename_passkey(
    session: SessionDep, authz: MayEnrol, audit: AuditDep, id: uuid.UUID, body: PasskeyRename
) -> PasskeyOut:
    user, _ = await _current(session, authz)
    row = await _own_passkey(session, user, id)
    before = _passkey_out(row)
    row.name = body.name
    after = _passkey_out(row)
    await audit.record(
        "account.passkey.rename", "passkey", str(id), before=snapshot(before), after=snapshot(after)
    )
    await session.commit()
    return after


@router.delete("/passkeys/{id}", status_code=204)
async def delete_passkey(
    session: SessionDep, authz: MayEnrol, audit: AuditDep, id: uuid.UUID
) -> None:
    """Remove a passkey. If it is your last factor, your recovery codes go too; you must have
    verified a factor in the last 10 minutes (403 reauth_required), and the org must not require
    MFA (409 mfa_required_by_org)."""
    user, current = await _current(session, authz)
    await _lock_user(session, user)
    row = await _own_passkey(session, user, id)
    factors = await mfa.user_factors(session, user.id)
    last = not factors.totp and factors.passkeys == 1
    if last:
        if await org_settings.require_mfa(session):
            raise mfa_required_by_org()
        if not mfa.recently_verified(current):
            raise reauth_required()
    before = _passkey_out(row)
    await session.delete(row)
    if last:
        await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await audit.record("account.passkey.delete", "passkey", str(id), before=snapshot(before))
    await session.commit()


@router.post("/recovery-codes")
async def regenerate_recovery_codes(
    session: SessionDep, authz: SignedIn, audit: AuditDep
) -> RecoveryCodes:
    """Replace your recovery codes with ten new ones, shown once. Needs a factor verified in the
    last 10 minutes (403 reauth_required)."""
    user, row = await _current(session, authz)
    if not mfa.has_factor(await mfa.user_factors(session, user.id)):
        raise forbidden("set up an authenticator app or a passkey first")
    if not mfa.recently_verified(row):
        raise reauth_required()
    codes = await mfa.replace_recovery_codes(session, user.id)
    await audit.record("account.recovery_codes.regenerate", "user", user.email)
    await session.commit()
    return RecoveryCodes(recovery_codes=codes)


@router.get("/tokens")
async def list_personal_tokens(session: SessionDep, authz: SignedIn) -> list[PersonalTokenOut]:
    """Your personal API tokens, oldest first, revoked and expired ones included."""
    assert authz.principal.user_id is not None
    return await api_tokens.personal_tokens_out(session, authz.principal.user_id)


@router.post("/tokens", status_code=201)
async def create_personal_token(
    session: SessionDep, authz: SignedIn, audit: AuditDep, body: PersonalTokenCreate
) -> PersonalTokenCreated:
    """Create a token that acts as you with `permissions`, within `project` (and `env`) when
    given. Each request it makes is also capped by your rights at that time. You can only grant
    what you hold there now (422 exceeds_owner_rights). The secret is returned only here."""
    user, _ = await _current(session, authz)
    owner = authz.authorizer
    project_id = environment_id = None
    if body.project is not None:
        project = await get_project(session, body.project)
        if not owner.can(Permission.PROJECT_READ, project.id):
            raise not_found("project", body.project)
        project_id = project.id
        if body.env is not None:
            environment = await get_environment(session, project, body.env)
            if not owner.can_see_environment(project.id, environment.id):
                raise not_found("environment", body.env)
            environment_id = environment.id
    permissions = sorted(set(body.permissions))
    refused = [p.value for p in permissions if not owner.can(p, project_id, environment_id)]
    if refused:
        raise exceeds_owner_rights(refused)
    row, secret = await api_tokens.create_personal_token(
        session,
        user,
        body.name,
        [p.value for p in permissions],
        project_id,
        environment_id,
        body.expires_at,
    )
    out = await api_tokens.personal_token_out(session, user.id, row.id)
    await audit.record("account.token.create", "token", str(row.id), after=snapshot(out))
    await session.commit()
    return PersonalTokenCreated(token=out, secret=secret)


@router.delete("/tokens/{id}", status_code=204)
async def revoke_personal_token(
    session: SessionDep, authz: SignedIn, audit: AuditDep, id: uuid.UUID
) -> None:
    """Revoke one of your personal tokens at once."""
    owner_id = authz.principal.user_id
    assert owner_id is not None
    row = await api_tokens.get_personal_token(session, owner_id, id)
    before = await api_tokens.personal_token_out(session, owner_id, id)
    await api_tokens.revoke(session, row)
    after = await api_tokens.personal_token_out(session, owner_id, id)
    await audit.record(
        "account.token.revoke", "token", str(id), before=snapshot(before), after=snapshot(after)
    )
    await session.commit()

"""Users (`/users`), managed by admins, and the public invitation links (`/invitations`)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from megooflags_api.auth import mfa
from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.auth.sessions import end_sessions
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES, ErrorBody
from megooflags_api.models import Invitation
from megooflags_api.models.enums import InvitationPurpose
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.identity import (
    InvitationAccept,
    InvitationOut,
    InviteLink,
    UserCreate,
    UserCreated,
    UserOut,
    UserUpdate,
)
from megooflags_api.services import invitations, users
from megooflags_api.services.audit import AuditDep, record, request_meta, snapshot
from megooflags_api.urls import public_base_url

router = APIRouter(prefix="/users", tags=["users"], responses=ERROR_RESPONSES)
# Public: the token in the path is the credential.
public_router = APIRouter(
    prefix="/invitations",
    tags=["invitations"],
    responses={404: {"model": ErrorBody}, 422: {"model": ErrorBody}},
)

ManageMembers = Annotated[Authz, Depends(require(Permission.MEMBER_MANAGE))]


def invite_link(request: Request, invitation: Invitation, token: str) -> InviteLink:
    return InviteLink(
        url=f"{public_base_url(request)}/invite/{token}", expires_at=invitation.expires_at
    )


@router.get("")
async def list_users(
    session: SessionDep,
    authz: ManageMembers,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[UserOut]:
    items, next_cursor = await users.list_users(session, q, cursor, limit)
    return Page[UserOut](items=items, next_cursor=next_cursor)


@router.post("", status_code=201)
async def create_user(
    session: SessionDep, request: Request, authz: ManageMembers, audit: AuditDep, body: UserCreate
) -> UserCreated:
    """Create a user without a password; they set one through the returned invite link."""
    user = await users.create_user(session, body.email, body.name, None)
    invitation, token = await invitations.issue(session, user, InvitationPurpose.INVITE)
    out = users.user_out(user)
    await audit.record("user.create", "user", user.email, after=snapshot(out))
    await session.commit()
    return UserCreated(user=out, invite=invite_link(request, invitation, token))


@router.get("/{id}")
async def read_user(session: SessionDep, authz: ManageMembers, id: uuid.UUID) -> UserOut:
    return users.user_out(await users.get_user(session, id))


@router.patch("/{id}")
async def update_user(
    session: SessionDep, authz: ManageMembers, audit: AuditDep, id: uuid.UUID, body: UserUpdate
) -> UserOut:
    user = await users.get_user(session, id)
    before = users.user_out(user)
    if body.name is not None:
        user.name = body.name
        await session.flush()
    out = users.user_out(user)
    await audit.record(
        "user.update", "user", user.email, before=snapshot(before), after=snapshot(out)
    )
    await session.commit()
    return out


@router.post("/{id}/disable")
async def disable_user(
    session: SessionDep, authz: ManageMembers, audit: AuditDep, id: uuid.UUID
) -> UserOut:
    """Disable the user and end all of their sessions; 409 last_admin for the last org admin."""
    user = await users.get_user(session, id)
    before = users.user_out(user)
    await users.disable_user(session, user)
    out = users.user_out(user)
    await audit.record(
        "user.disable", "user", user.email, before=snapshot(before), after=snapshot(out)
    )
    await session.commit()
    return out


@router.post("/{id}/enable")
async def enable_user(
    session: SessionDep, authz: ManageMembers, audit: AuditDep, id: uuid.UUID
) -> UserOut:
    user = await users.get_user(session, id)
    before = users.user_out(user)
    await users.enable_user(session, user)
    out = users.user_out(user)
    await audit.record(
        "user.enable", "user", user.email, before=snapshot(before), after=snapshot(out)
    )
    await session.commit()
    return out


@router.post("/{id}/reset-link")
async def reset_link(
    session: SessionDep, request: Request, authz: ManageMembers, audit: AuditDep, id: uuid.UUID
) -> InviteLink:
    """A password reset link for the user; older links stop working."""
    user = await users.get_user(session, id)
    out = users.user_out(user)
    invitation, token = await invitations.issue(session, user, InvitationPurpose.RESET)
    await audit.record(
        "user.reset_link", "user", user.email, before=snapshot(out), after=snapshot(out)
    )
    await session.commit()
    return invite_link(request, invitation, token)


@router.delete("/{id}/mfa", status_code=204)
async def reset_mfa(
    session: SessionDep, authz: ManageMembers, audit: AuditDep, id: uuid.UUID
) -> Response:
    """Remove all of the user's second factors and recovery codes, and end their sessions; they
    sign in with their password alone next time (and enrol again if the org requires MFA)."""
    user = await users.get_user(session, id)
    before = await mfa.user_factors(session, user.id)
    await mfa.clear_factors(session, user.id)
    await end_sessions(session, user.id)
    after = await mfa.user_factors(session, user.id)
    await audit.record(
        "user.mfa_reset", "user", user.email, before=snapshot(before), after=snapshot(after)
    )
    await session.commit()
    return Response(status_code=204)


@public_router.get("/{token}")
async def read_invitation(session: SessionDep, token: str) -> InvitationOut:
    invitation, user = await invitations.find_valid(session, token)
    return InvitationOut(
        email=user.email,
        name=user.name,
        purpose=invitation.purpose,
        expires_at=invitation.expires_at,
    )


@public_router.post("/{token}", status_code=204)
async def accept_invitation(
    session: SessionDep, request: Request, token: str, body: InvitationAccept
) -> Response:
    """Set the password, use up the link and end all of the user's sessions."""
    invitation, user = await invitations.find_valid(session, token, for_update=True)
    before = users.user_out(user)
    await invitations.accept(session, invitation, user, body.password)
    await record(
        session,
        None,
        request_meta(request),
        "invitation.accept",
        "user",
        user.email,
        before=snapshot(before),
        after=snapshot(users.user_out(user)),
        actor_label=user.email,
    )
    await session.commit()
    return Response(status_code=204)

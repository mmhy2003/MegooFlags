"""Invitation and password reset links: a one-time token that lets a user set their password.

Only the sha256 of the token is stored. A user has at most one usable link: issuing a new one
marks the older ones as used.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.sessions import end_sessions
from megooflags_api.errors import invalid_invitation
from megooflags_api.models import Invitation, User
from megooflags_api.models.enums import InvitationPurpose
from megooflags_api.services.users import set_password

VALID_FOR = timedelta(days=7)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue(
    session: AsyncSession, user: User, purpose: InvitationPurpose
) -> tuple[Invitation, str]:
    """A new link for `user`, valid for 7 days; returns it with its token. Flushes."""
    now = datetime.now(UTC)
    await session.execute(
        update(Invitation)
        .where(Invitation.user_id == user.id, Invitation.used_at.is_(None))
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    row = Invitation(
        token_hash=_hash(token), user_id=user.id, purpose=purpose, expires_at=now + VALID_FOR
    )
    session.add(row)
    await session.flush()
    return row, token


async def find_valid(
    session: AsyncSession, token: str, *, for_update: bool = False
) -> tuple[Invitation, User]:
    """The unused, unexpired link for `token` and its user; 404 invalid_invitation otherwise.

    `for_update` locks the row, so two concurrent uses cannot both succeed.
    """
    query = (
        select(Invitation, User)
        .join(User, User.id == Invitation.user_id)
        .where(
            Invitation.token_hash == _hash(token),
            Invitation.used_at.is_(None),
            Invitation.expires_at > datetime.now(UTC),
        )
    )
    if for_update:
        query = query.with_for_update(of=Invitation)
    found = (await session.execute(query)).one_or_none()
    if found is None:
        raise invalid_invitation()
    invitation, user = found
    return invitation, user


async def accept(session: AsyncSession, invitation: Invitation, user: User, password: str) -> None:
    """Set the password (422 weak_password first, leaving the link usable), use up the link and
    end all of the user's sessions. Flushes; the caller commits."""
    await set_password(session, user, password)
    invitation.used_at = datetime.now(UTC)
    await end_sessions(session, user.id)
    await session.flush()

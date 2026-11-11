"""Users: lookup by id or email (case-insensitive), listing, creation, password changes and
disabling."""

import uuid

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.passwords import check_password_policy, hash_password
from megooflags_api.auth.sessions import end_sessions
from megooflags_api.errors import key_conflict, not_found
from megooflags_api.models import User
from megooflags_api.models.enums import UserStatus
from megooflags_api.schemas.identity import UserOut
from megooflags_api.services.bindings import active_org_admin_ids, guard_last_admin
from megooflags_api.services.pagination import decode_cursor, page_slice


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.email) == email.lower()))


async def has_users(session: AsyncSession) -> bool:
    return bool(await session.scalar(select(exists().select_from(User))))


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise not_found("user", str(user_id))
    return user


async def list_users(
    session: AsyncSession, q: str | None, cursor: str | None, limit: int
) -> tuple[list[UserOut], str | None]:
    """Users by lower(email); `q` matches part of the email or name, ignoring case."""
    sort_key = func.lower(User.email)
    query = select(User).order_by(sort_key).limit(limit + 1)
    if q:
        query = query.where(
            or_(User.email.icontains(q, autoescape=True), User.name.icontains(q, autoescape=True))
        )
    if cursor is not None:
        query = query.where(sort_key > decode_cursor(cursor))
    rows = list(await session.scalars(query))
    page, next_cursor = page_slice(rows, limit, lambda u: u.email.lower())
    return [user_out(u) for u in page], next_cursor


async def create_user(session: AsyncSession, email: str, name: str, password: str | None) -> User:
    """Create a user. A user without a password (None) can sign in only through SSO."""
    if password is not None:
        check_password_policy(password)
    if await get_user_by_email(session, email) is not None:
        raise key_conflict("user", email)
    user = User(
        email=email,
        name=name,
        password_hash=None if password is None else hash_password(password),
    )
    session.add(user)
    await session.flush()
    return user


async def set_password(session: AsyncSession, user: User, password: str) -> None:
    check_password_policy(password)
    user.password_hash = hash_password(password)
    await session.flush()


async def disable_user(session: AsyncSession, user: User) -> None:
    """Disable a user and end their sessions; 409 last_admin if they are the last org admin."""
    was_admin = user.id in await active_org_admin_ids(session)
    user.status = UserStatus.DISABLED
    await end_sessions(session, user.id)
    await session.flush()
    if was_admin:
        await guard_last_admin(session)


async def enable_user(session: AsyncSession, user: User) -> None:
    user.status = UserStatus.ACTIVE
    await session.flush()


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        status=user.status,
        has_password=user.password_hash is not None,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )

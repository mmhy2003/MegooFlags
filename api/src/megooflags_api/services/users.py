"""Users: lookup by id or email (case-insensitive), creation and password changes."""

import uuid

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.passwords import check_password_policy, hash_password
from megooflags_api.errors import key_conflict, not_found
from megooflags_api.models import User
from megooflags_api.schemas.identity import UserOut


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.email) == email.lower()))


async def has_users(session: AsyncSession) -> bool:
    return bool(await session.scalar(select(exists().select_from(User))))


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise not_found("user", str(user_id))
    return user


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

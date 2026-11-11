import uuid

import pytest
from fastapi import FastAPI

from megooflags_api.auth.passwords import verify_password
from megooflags_api.errors import ApiError
from megooflags_api.models.enums import UserStatus
from megooflags_api.services.users import (
    create_user,
    get_user,
    get_user_by_email,
    set_password,
    user_out,
)

PASSWORD = "correct horse battery staple"


async def test_create_then_find_by_email_in_another_case(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        user = await create_user(session, "Ada@Example.com", "Ada", PASSWORD)

        found = await get_user_by_email(session, "ada@EXAMPLE.com")
        assert found is not None
        assert found.id == user.id
        assert await get_user_by_email(session, "bob@example.com") is None
        assert (await get_user(session, user.id)).id == user.id

        out = user_out(user)
        assert out.email == "Ada@Example.com"
        assert out.name == "Ada"
        assert out.status == UserStatus.ACTIVE
        assert out.has_password
        assert out.last_login_at is None


async def test_duplicate_email_in_another_case_conflicts(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        await create_user(session, "ada@example.com", "Ada", PASSWORD)

        with pytest.raises(ApiError) as excinfo:
            await create_user(session, "ADA@example.com", "Ada again", PASSWORD)

    assert excinfo.value.status == 409
    assert excinfo.value.code == "key_conflict"


async def test_create_checks_the_password_policy(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        with pytest.raises(ApiError) as excinfo:
            await create_user(session, "ada@example.com", "Ada", "short")

    assert excinfo.value.code == "weak_password"


async def test_user_without_password(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        user = await create_user(session, "sso@example.com", "SSO", None)

        assert user.password_hash is None
        assert not user_out(user).has_password


async def test_get_unknown_user_is_not_found(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        with pytest.raises(ApiError) as excinfo:
            await get_user(session, uuid.uuid4())

    assert excinfo.value.status == 404


async def test_set_password_changes_the_hash(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        user = await create_user(session, "ada@example.com", "Ada", PASSWORD)
        before = user.password_hash

        await set_password(session, user, "another long passphrase")

        assert user.password_hash != before
        assert user.password_hash is not None
        assert verify_password(user.password_hash, "another long passphrase")

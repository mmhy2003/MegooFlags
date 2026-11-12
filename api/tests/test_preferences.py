"""Per-user UI preferences: theme and sidebar state."""

from fastapi import FastAPI
from helpers import seed_user

from megooflags_api.models import User
from megooflags_api.models.enums import UserTheme


async def test_new_user_has_default_preferences(app: FastAPI) -> None:
    uid = await seed_user(app)

    async with app.state.sessionmaker() as session:
        user = await session.get(User, uid)

    assert user is not None
    assert user.theme == UserTheme.SYSTEM
    assert user.sidebar_collapsed is False

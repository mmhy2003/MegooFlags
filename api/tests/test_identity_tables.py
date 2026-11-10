import pytest
from fastapi import FastAPI
from helpers import reset_database
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from megooflags_api.auth.rbac import BUILTIN_ROLES
from megooflags_api.models import OrgSettings, Role, RoleBinding, User
from megooflags_api.models.enums import ScopeKind, SubjectKind


async def test_builtin_roles_are_seeded(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        roles = (await session.scalars(select(Role))).all()

    assert {r.key for r in roles} == {"admin", "editor", "viewer"}
    for role in roles:
        assert role.builtin
        assert set(role.permissions) == set(BUILTIN_ROLES[role.key])


async def test_org_settings_has_one_row(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        rows = (await session.scalars(select(OrgSettings))).all()

    assert [(r.id, r.require_mfa) for r in rows] == [(1, False)]


async def test_project_binding_needs_a_project(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        admin = await session.scalar(select(Role).where(Role.key == "admin"))
        assert admin is not None
        user = User(email="a@example.com", name="A")
        session.add(user)
        await session.flush()
        session.add(
            RoleBinding(
                subject_kind=SubjectKind.USER,
                subject_id=user.id,
                role_id=admin.id,
                scope=ScopeKind.PROJECT,
            )
        )
        with pytest.raises(IntegrityError, match="ck_role_binding_scope_matches"):
            await session.flush()


async def test_email_is_unique_regardless_of_case(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        session.add(User(email="Ada@Example.com", name="Ada"))
        await session.flush()
        session.add(User(email="ada@example.com", name="Ada"))
        with pytest.raises(IntegrityError, match="uq_user_email_lower"):
            await session.flush()


async def test_reset_database_keeps_only_builtin_state(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        session.add(User(email="a@example.com", name="A"))
        session.add(Role(key="custom", name="Custom", permissions=["project.read"]))
        await session.execute(text("UPDATE org_settings SET require_mfa = true"))
        await session.commit()

    await reset_database(app)

    async with app.state.sessionmaker() as session:
        assert await session.scalar(text('SELECT count(*) FROM "user"')) == 0
        assert set(await session.scalars(select(Role.key))) == {"admin", "editor", "viewer"}
        assert await session.scalar(select(OrgSettings.require_mfa)) is False

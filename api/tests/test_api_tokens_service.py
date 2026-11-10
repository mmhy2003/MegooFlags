import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from helpers import count_statements, seed_service_token, seed_user
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from megooflags_api.models import ApiToken, Role, RoleBinding, User
from megooflags_api.models.enums import ApiTokenKind, ScopeKind, SdkKeyKind, SubjectKind
from megooflags_api.services.api_tokens import (
    create_personal_token,
    create_service_token,
    generate_token,
    looks_like_token,
    resolve_token,
    touch_last_used,
)
from megooflags_api.services.sdk_keys import generate_key


def test_generate_token_format() -> None:
    tokens = {generate_token() for _ in range(20)}
    assert len(tokens) == 20
    for token in tokens:
        assert re.fullmatch(r"mf_api_[0-9A-Za-z]{32}", token)
        assert looks_like_token(token)


def test_looks_like_token_rejects_other_shapes() -> None:
    assert not looks_like_token(generate_key(SdkKeyKind.SERVER))
    assert not looks_like_token("mf_api_short")
    assert not looks_like_token("mf_api_" + "!" * 32)


async def new_service_token(
    app: FastAPI, *, expires_at: datetime | None = None
) -> tuple[ApiToken, str]:
    async with app.state.sessionmaker() as session:
        row, secret = await create_service_token(
            session, "ci", created_by=None, expires_at=expires_at
        )
        await session.commit()
    return row, secret


async def test_create_service_token_stores_only_the_hash(app: FastAPI) -> None:
    row, secret = await new_service_token(app)

    async with app.state.sessionmaker() as session:
        stored = await session.get_one(ApiToken, row.id)
    assert stored.kind == ApiTokenKind.SERVICE
    assert stored.name == "ci"
    assert stored.prefix == secret[:14]
    assert stored.secret_hash == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in (stored.prefix, stored.secret_hash)
    assert stored.owner_user_id is None
    assert stored.last_used_at is None


async def test_create_personal_token(app: FastAPI) -> None:
    user_id = await seed_user(app)
    async with app.state.sessionmaker() as session:
        owner = await session.get_one(User, user_id)
        row, secret = await create_personal_token(
            session, owner, "laptop", ["project.read"], None, None, None
        )
        await session.commit()
        found = await resolve_token(session, secret)

    assert found is not None
    assert found.id == row.id
    assert found.kind == ApiTokenKind.PERSONAL
    assert found.owner_user_id == user_id
    assert found.created_by == user_id
    assert found.permissions == ["project.read"]


async def test_resolve_token_finds_the_token(app: FastAPI) -> None:
    row, secret = await new_service_token(app)

    async with app.state.sessionmaker() as session:
        with count_statements(app.state.engine) as statements:
            found = await resolve_token(session, secret)

    assert found is not None
    assert found.id == row.id
    assert len(statements) == 1


async def test_resolve_token_ignores_unknown_and_sdk_keys(app: FastAPI) -> None:
    await new_service_token(app)

    async with app.state.sessionmaker() as session:
        assert await resolve_token(session, generate_token()) is None
        with count_statements(app.state.engine) as statements:
            assert await resolve_token(session, generate_key(SdkKeyKind.SERVER)) is None
        assert statements == []


async def test_resolve_token_refuses_revoked_and_expired_tokens(app: FastAPI) -> None:
    revoked, revoked_secret = await new_service_token(app)
    _, expired_secret = await new_service_token(
        app, expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    _, future_secret = await new_service_token(
        app, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(ApiToken).where(ApiToken.id == revoked.id).values(revoked_at=datetime.now(UTC))
        )
        await session.commit()

    async with app.state.sessionmaker() as session:
        assert await resolve_token(session, revoked_secret) is None
        assert await resolve_token(session, expired_secret) is None
        assert await resolve_token(session, future_secret) is not None


async def test_touch_last_used_writes_at_most_every_5_minutes(app: FastAPI) -> None:
    row, secret = await new_service_token(app)

    async with app.state.sessionmaker() as session:
        found = await resolve_token(session, secret)
        assert found is not None
        before = datetime.now(UTC)
        await touch_last_used(session, found)
    async with app.state.sessionmaker() as session:
        stored = await session.get_one(ApiToken, row.id)
    assert stored.last_used_at is not None
    assert stored.last_used_at >= before

    async with app.state.sessionmaker() as session:
        found = await resolve_token(session, secret)
        assert found is not None
        with count_statements(app.state.engine) as statements:
            await touch_last_used(session, found)
        assert statements == []

    used = datetime.now(UTC) - timedelta(minutes=5, seconds=1)
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(ApiToken).where(ApiToken.id == row.id).values(last_used_at=used)
        )
        await session.commit()
    async with app.state.sessionmaker() as session:
        found = await resolve_token(session, secret)
        assert found is not None
        before = datetime.now(UTC)
        await touch_last_used(session, found)
    async with app.state.sessionmaker() as session:
        stored = await session.get_one(ApiToken, row.id)
    assert stored.last_used_at is not None
    assert stored.last_used_at >= before


async def test_personal_token_needs_an_owner(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        session.add(
            ApiToken(
                kind=ApiTokenKind.PERSONAL,
                name="orphan",
                prefix="mf_api_xxxxxxx",
                secret_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_seed_service_token_has_an_admin_org_binding(app: FastAPI) -> None:
    secret = await seed_service_token(app)

    async with app.state.sessionmaker() as session:
        token = await resolve_token(session, secret)
        assert token is not None
        assert token.last_used_at is not None
        bindings = (
            await session.execute(
                select(Role.key, RoleBinding.scope)
                .join(Role, Role.id == RoleBinding.role_id)
                .where(
                    RoleBinding.subject_kind == SubjectKind.SERVICE_TOKEN,
                    RoleBinding.subject_id == token.id,
                )
            )
        ).all()
    assert [tuple(b) for b in bindings] == [("admin", ScopeKind.ORG)]

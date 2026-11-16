import hashlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update

from megooflags_api.errors import ApiError
from megooflags_api.models import Node, NodeToken
from megooflags_api.models.enums import NodeStatus
from megooflags_api.services.cluster import nodes

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def node(**fields: object) -> Node:
    return Node(name="worker-1", **fields)


def status(n: Node, active_tokens: int = 1) -> NodeStatus:
    return nodes.node_status(n, active_tokens=active_tokens, now=NOW)


def test_status_pending() -> None:
    assert status(node()) == NodeStatus.PENDING


def test_status_healthy() -> None:
    assert status(node(last_heartbeat_at=NOW - timedelta(seconds=5))) == NodeStatus.HEALTHY


def test_status_stale_after_30_s() -> None:
    assert status(node(last_heartbeat_at=NOW - timedelta(seconds=30))) == NodeStatus.HEALTHY
    assert status(node(last_heartbeat_at=NOW - timedelta(seconds=31))) == NodeStatus.STALE


def test_status_draining_wins_over_healthy() -> None:
    n = node(last_heartbeat_at=NOW, drain_requested_at=NOW - timedelta(seconds=1))
    assert status(n) == NodeStatus.DRAINING


def test_status_incompatible_until_next_heartbeat() -> None:
    refused = NOW - timedelta(seconds=10)
    assert status(node(refused_version="0.9.0", refused_at=refused)) == NodeStatus.INCOMPATIBLE
    earlier = node(
        refused_at=refused,
        last_heartbeat_at=refused - timedelta(seconds=5),
        drain_requested_at=NOW,
    )
    assert status(earlier) == NodeStatus.INCOMPATIBLE
    later = node(refused_at=refused, last_heartbeat_at=refused + timedelta(seconds=5))
    assert status(later) == NodeStatus.HEALTHY


def test_status_revoked_wins() -> None:
    n = node(
        revoked_at=NOW,
        refused_at=NOW,
        drain_requested_at=NOW,
        last_heartbeat_at=NOW - timedelta(seconds=1),
    )
    assert status(n) == NodeStatus.REVOKED
    assert status(node(last_heartbeat_at=NOW), active_tokens=0) == NodeStatus.REVOKED


def test_syncing() -> None:
    n = node(last_heartbeat_at=NOW, snapshot_version=4)
    assert nodes.is_syncing(n, NodeStatus.HEALTHY, 5)
    assert not nodes.is_syncing(n, NodeStatus.HEALTHY, 4)
    assert not nodes.is_syncing(n, NodeStatus.STALE, 5)
    assert nodes.is_syncing(node(last_heartbeat_at=NOW), NodeStatus.HEALTHY, 1)


async def tokens_of(app: FastAPI, node_id: object) -> list[NodeToken]:
    async with app.state.sessionmaker() as session:
        rows = await session.scalars(
            select(NodeToken).where(NodeToken.node_id == node_id).order_by(NodeToken.created_at)
        )
        return list(rows.all())


async def test_create_returns_token_once_and_stores_hash(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        row, token = await nodes.create_node(session, "worker-1")
        await session.commit()
    assert re.fullmatch(r"mf_node_[0-9A-Za-z]{32}", token)
    assert row.name == "worker-1"
    [stored] = await tokens_of(app, row.id)
    assert stored.secret_hash == hashlib.sha256(token.encode()).hexdigest()
    assert stored.prefix == token[:12]
    assert stored.revoked_at is None and stored.expires_at is None


async def test_duplicate_name_409(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        await nodes.create_node(session, "worker-1")
        await session.commit()
        with pytest.raises(ApiError) as exc:
            await nodes.create_node(session, "worker-1")
    assert (exc.value.status, exc.value.code) == (409, "key_conflict")


async def test_authenticate_token(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        row, token = await nodes.create_node(session, "worker-1")
        _, expired = await nodes.create_node(session, "worker-2")
        await session.commit()

        assert await nodes.authenticate_token(session, nodes.generate_node_token()) is None
        assert await nodes.authenticate_token(session, "not-a-token") is None
        auth = await nodes.authenticate_token(session, token)
        assert auth is not None
        assert (auth.node.id, auth.active) == (row.id, True)
        [stored] = await tokens_of(app, row.id)
        assert auth.token_id == stored.id

        await session.execute(
            update(NodeToken)
            .where(NodeToken.secret_hash == nodes.hash_token(expired))
            .values(expires_at=func.now() - timedelta(seconds=1))
        )
        await nodes.revoke_node(session, row)
        await session.commit()

        revoked = await nodes.authenticate_token(session, token)
        assert revoked is not None and not revoked.active
        lapsed = await nodes.authenticate_token(session, expired)
        assert lapsed is not None and not lapsed.active


async def test_rotate_overlap_keeps_old_until_expiry(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        row, old = await nodes.create_node(session, "worker-1")
        await session.commit()
        now = await session.scalar(select(func.now()))  # the rotation's transaction time
        assert now is not None
        new = await nodes.rotate_token(session, row, timedelta(minutes=10))
        await session.commit()

        assert new != old
        old_row, new_row = await tokens_of(app, row.id)
        assert old_row.expires_at == now + timedelta(minutes=10)
        assert old_row.revoked_at is None
        assert new_row.expires_at is None and new_row.prefix == new[:12]
        for token in (old, new):
            auth = await nodes.authenticate_token(session, token)
            assert auth is not None and auth.active

        # A second rotation never extends the old token's expiry.
        await nodes.rotate_token(session, row, timedelta(hours=1))
        await session.commit()
        old_again, *_ = await tokens_of(app, row.id)
        assert old_again.expires_at == old_row.expires_at


async def test_rotate_zero_revokes_old(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        row, old = await nodes.create_node(session, "worker-1")
        await session.commit()
        new = await nodes.rotate_token(session, row, timedelta(0))
        await session.commit()

        old_auth = await nodes.authenticate_token(session, old)
        assert old_auth is not None and not old_auth.active
        new_auth = await nodes.authenticate_token(session, new)
        assert new_auth is not None and new_auth.active


async def test_revoke_revokes_all_tokens(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        row, _ = await nodes.create_node(session, "worker-1")
        await session.commit()
        await nodes.rotate_token(session, row, timedelta(0))
        await nodes.rotate_token(session, row, timedelta(minutes=5))
        await session.commit()
        first_revoked = (await tokens_of(app, row.id))[0].revoked_at

        await nodes.revoke_node(session, row)
        await session.commit()
        assert row.revoked_at is not None
        tokens = await tokens_of(app, row.id)
        assert len(tokens) == 3
        assert all(t.revoked_at is not None for t in tokens)
        assert tokens[0].revoked_at == first_revoked

        revoked_at = row.revoked_at
        await nodes.revoke_node(session, row)
        await session.commit()
        assert row.revoked_at == revoked_at


async def test_drain_resume(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        row, _ = await nodes.create_node(session, "worker-1")
        await session.commit()
        await nodes.drain_node(session, row)
        await session.commit()
        drained = await nodes.get_node(session, row.id)
        assert drained.drain_requested_at is not None

        await nodes.resume_node(session, row)
        await session.commit()
        resumed = await nodes.get_node(session, row.id)
        assert resumed.drain_requested_at is None


async def test_operations_on_revoked_409(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        row, _ = await nodes.create_node(session, "worker-1")
        await nodes.revoke_node(session, row)
        await session.commit()
        for operation in (
            nodes.rotate_token(session, row, timedelta(0)),
            nodes.drain_node(session, row),
            nodes.resume_node(session, row),
        ):
            with pytest.raises(ApiError) as exc:
                await operation
            assert (exc.value.status, exc.value.code) == (409, "invalid_state")


async def test_delete_requires_revoked(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        row, _ = await nodes.create_node(session, "worker-1")
        await session.commit()
        with pytest.raises(ApiError) as exc:
            await nodes.delete_node(session, row)
        assert (exc.value.status, exc.value.code) == (409, "invalid_state")
        assert exc.value.message == "revoke the node before removing it"

        await nodes.revoke_node(session, row)
        await nodes.delete_node(session, row)
        await session.commit()
        with pytest.raises(ApiError) as missing:
            await nodes.get_node(session, row.id)
        assert (missing.value.status, missing.value.code) == (404, "not_found")
        assert await tokens_of(app, row.id) == []


async def test_list_counts_active_tokens(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        b, _ = await nodes.create_node(session, "worker-b")
        a, _ = await nodes.create_node(session, "worker-a")
        c, _ = await nodes.create_node(session, "worker-c")
        await session.commit()
        newest = await nodes.rotate_token(session, a, timedelta(minutes=5))
        await nodes.revoke_node(session, c)
        await session.commit()

        listed = await nodes.list_nodes(session)

    assert [(n.name, count) for n, count, _ in listed] == [
        ("worker-a", 2),
        ("worker-b", 1),
        ("worker-c", 0),
    ]
    assert listed[0][2] == newest[:12]
    assert listed[2][2] is None

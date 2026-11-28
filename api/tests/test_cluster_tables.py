import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from helpers import create_project
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from megooflags_api.models import Environment, EnvironmentSnapshot, Node, NodeEventBatch, NodeToken


async def test_node_name_unique(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        session.add(Node(name="worker-1"))
        await session.flush()
        session.add(Node(name="worker-1"))
        with pytest.raises(IntegrityError, match="uq_node_name"):
            await session.flush()


async def test_node_token_cascades_on_node_delete(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        node = Node(name="worker-1")
        session.add(node)
        await session.flush()
        session.add(NodeToken(node_id=node.id, prefix="mf_node_abcd", secret_hash="a" * 64))
        await session.commit()

        await session.execute(delete(Node).where(Node.id == node.id))
        await session.commit()

        assert (await session.scalars(select(NodeToken))).all() == []


async def test_environment_snapshot_cascades_on_environment_delete(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    async with app.state.sessionmaker() as session:
        environment_id = (await session.scalars(select(Environment.id))).first()
        assert environment_id is not None
        session.add(
            EnvironmentSnapshot(
                environment_id=environment_id,
                version=1,
                digest="b" * 64,
                published_at=datetime.now(UTC),
            )
        )
        await session.commit()

        await session.execute(
            text("DELETE FROM environment WHERE id = :id"), {"id": environment_id}
        )
        await session.commit()

        assert (await session.scalars(select(EnvironmentSnapshot))).all() == []


async def test_snapshot_version_sequence_increases(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        first = await session.scalar(text("SELECT nextval('snapshot_version_seq')"))
        second = await session.scalar(text("SELECT nextval('snapshot_version_seq')"))

    assert first is not None and second is not None
    assert second > first


async def test_event_batch_primary_key(app: FastAPI) -> None:
    batch_id = uuid.uuid4()
    async with app.state.sessionmaker() as session:
        node = Node(name="worker-1")
        session.add(node)
        await session.flush()
        session.add(NodeEventBatch(batch_id=batch_id, node_id=node.id))
        await session.commit()

        session.add(NodeEventBatch(batch_id=batch_id, node_id=node.id))
        with pytest.raises(IntegrityError, match="pk_node_event_batch"):
            await session.flush()

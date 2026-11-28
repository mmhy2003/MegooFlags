"""Node registry: worker nodes, their rotating join tokens and their computed status.

Only the SHA-256 of a token is stored; the secret is shown once, on create and on rotate.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import invalid_state, key_conflict, not_found
from megooflags_api.models import Node, NodeToken
from megooflags_api.models.enums import NodeStatus
from megooflags_api.services.cluster.config import NODE_TOKEN_PREFIX
from megooflags_api.services.sdk_keys import BASE62, is_inactive

NODE_TOKEN_LENGTH = 40  # "mf_node_" + 32 base62
TOKEN_DISPLAY_PREFIX = 12
HEARTBEAT_FRESH = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class NodeAuth:
    node: Node
    token_id: uuid.UUID
    active: bool


def generate_node_token() -> str:
    length = NODE_TOKEN_LENGTH - len(NODE_TOKEN_PREFIX)
    return NODE_TOKEN_PREFIX + "".join(secrets.choice(BASE62) for _ in range(length))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def node_status(node: Node, *, active_tokens: int, now: datetime) -> NodeStatus:
    """The first that applies: revoked, incompatible, draining, pending, stale, healthy."""
    if node.revoked_at is not None or active_tokens == 0:
        return NodeStatus.REVOKED
    if node.refused_at is not None and (
        node.last_heartbeat_at is None or node.refused_at > node.last_heartbeat_at
    ):
        return NodeStatus.INCOMPATIBLE
    if node.drain_requested_at is not None:
        return NodeStatus.DRAINING
    if node.last_heartbeat_at is None:
        return NodeStatus.PENDING
    if now - node.last_heartbeat_at > HEARTBEAT_FRESH:
        return NodeStatus.STALE
    return NodeStatus.HEALTHY


def is_syncing(node: Node, status: NodeStatus, current_version: int) -> bool:
    """A healthy node that has not yet reported the current snapshot version."""
    return status == NodeStatus.HEALTHY and (node.snapshot_version or 0) < current_version


async def _now(session: AsyncSession) -> datetime:
    now: datetime | None = await session.scalar(select(func.now()))
    assert now is not None
    return now


async def _add_token(session: AsyncSession, node: Node) -> str:
    token = generate_node_token()
    session.add(
        NodeToken(
            node_id=node.id, prefix=token[:TOKEN_DISPLAY_PREFIX], secret_hash=hash_token(token)
        )
    )
    await session.flush()
    return token


async def create_node(session: AsyncSession, name: str) -> tuple[Node, str]:
    node = Node(name=name)
    session.add(node)
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("node", name) from None
        raise
    return node, await _add_token(session, node)


async def list_nodes(session: AsyncSession) -> list[tuple[Node, int, str | None]]:
    """Every node by name, with its number of active tokens and the newest active prefix."""
    rows = (await session.scalars(select(Node).order_by(Node.name))).all()
    now = await _now(session)
    tokens = await session.execute(
        select(NodeToken.node_id, NodeToken.prefix)
        .where(
            NodeToken.revoked_at.is_(None),
            or_(NodeToken.expires_at.is_(None), NodeToken.expires_at > now),
        )
        .order_by(NodeToken.created_at.desc())
    )
    counts: dict[uuid.UUID, int] = {}
    newest: dict[uuid.UUID, str] = {}
    for node_id, prefix in tokens:
        counts[node_id] = counts.get(node_id, 0) + 1
        newest.setdefault(node_id, prefix)
    return [(n, counts.get(n.id, 0), newest.get(n.id)) for n in rows]


async def get_node(session: AsyncSession, node_id: uuid.UUID, *, for_update: bool = False) -> Node:
    query = select(Node).where(Node.id == node_id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    node = await session.scalar(query)
    if node is None:
        raise not_found("node", str(node_id))
    return node


def _ensure_not_revoked(node: Node) -> None:
    if node.revoked_at is not None:
        raise invalid_state("the node is revoked")


async def rotate_token(session: AsyncSession, node: Node, overlap: timedelta) -> str:
    """Add a new token. Active tokens keep working for `overlap` (never longer than they
    already would); a zero overlap revokes them at once. Returns the new secret."""
    _ensure_not_revoked(node)
    now = await _now(session)
    active = (
        NodeToken.node_id == node.id,
        NodeToken.revoked_at.is_(None),
        or_(NodeToken.expires_at.is_(None), NodeToken.expires_at > now),
    )
    if overlap <= timedelta(0):
        await session.execute(update(NodeToken).where(*active).values(revoked_at=now))
    else:
        until = now + overlap
        await session.execute(
            update(NodeToken)
            .where(*active)
            .values(expires_at=func.least(func.coalesce(NodeToken.expires_at, until), until))
        )
    return await _add_token(session, node)


async def revoke_node(session: AsyncSession, node: Node) -> None:
    """Revoke the node and every token. Revoking twice keeps the first revoked_at."""
    now = await _now(session)
    if node.revoked_at is None:
        node.revoked_at = now
    await session.execute(
        update(NodeToken)
        .where(NodeToken.node_id == node.id, NodeToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.flush()


async def drain_node(session: AsyncSession, node: Node) -> None:
    _ensure_not_revoked(node)
    if node.drain_requested_at is None:
        node.drain_requested_at = await _now(session)
        await session.flush()


async def resume_node(session: AsyncSession, node: Node) -> None:
    _ensure_not_revoked(node)
    node.drain_requested_at = None
    await session.flush()


async def delete_node(session: AsyncSession, node: Node) -> None:
    """Only a revoked node can be removed; its tokens go with it."""
    if node.revoked_at is None:
        raise invalid_state("revoke the node before removing it")
    await session.delete(node)
    await session.flush()


async def authenticate_token(session: AsyncSession, token: str) -> NodeAuth | None:
    """The token's node, or None for an unknown token. A revoked or expired token, or a token
    of a revoked node, is found but not active."""
    if len(token) != NODE_TOKEN_LENGTH or not token.startswith(NODE_TOKEN_PREFIX):
        return None
    row = (
        await session.execute(
            select(Node, NodeToken.id, NodeToken.revoked_at, NodeToken.expires_at)
            .join(NodeToken, NodeToken.node_id == Node.id)
            .where(NodeToken.secret_hash == hash_token(token))
        )
    ).one_or_none()
    if row is None:
        return None
    node, token_id, revoked_at, expires_at = row
    now = await _now(session)
    active = node.revoked_at is None and not is_inactive(revoked_at, expires_at, now)
    return NodeAuth(node=node, token_id=token_id, active=active)

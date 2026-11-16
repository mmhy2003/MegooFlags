"""Worker nodes (`/nodes`), managed on a control plane only."""

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api import __version__
from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES, not_control_plane
from megooflags_api.models import Node
from megooflags_api.models.enums import NodeStatus
from megooflags_api.schemas.common import RotateIn
from megooflags_api.schemas.nodes import NodeCreate, NodeCreated, NodeList, NodeOut
from megooflags_api.services.audit import AuditDep, snapshot
from megooflags_api.services.changes import ChangesDep
from megooflags_api.services.cluster import nodes
from megooflags_api.services.cluster.snapshots import current_snapshot_version

router = APIRouter(prefix="/nodes", tags=["nodes"], responses=ERROR_RESPONSES)

ManageOrg = Annotated[Authz, Depends(require(Permission.ORG_MANAGE))]


def require_control_plane(request: Request, authz: ManageOrg) -> Authz:
    """ORG_MANAGE first, so callers without it never learn the role; then the role."""
    if request.app.state.settings.role != "control-plane":
        raise not_control_plane()
    return authz


ManageNodes = Annotated[Authz, Depends(require_control_plane)]


async def _outs(session: AsyncSession) -> tuple[int, list[NodeOut]]:
    """The current snapshot version and every node, by name."""
    current = await current_snapshot_version(session)
    now = await session.scalar(select(func.now()))
    assert now is not None
    outs = []
    for node, active_tokens, prefix in await nodes.list_nodes(session):
        status = nodes.node_status(node, active_tokens=active_tokens, now=now)
        outs.append(_node_out(node, status, nodes.is_syncing(node, status, current), prefix))
    return current, outs


def _node_out(node: Node, status: NodeStatus, syncing: bool, prefix: str | None) -> NodeOut:
    return NodeOut(
        id=node.id,
        name=node.name,
        status=status,
        syncing=syncing,
        created_at=node.created_at,
        last_heartbeat_at=node.last_heartbeat_at,
        synced_at=node.synced_at,
        version=node.version,
        snapshot_version=node.snapshot_version,
        sdk_streams=node.sdk_streams,
        buffered_events=node.buffered_events,
        address=node.address,
        refused_version=node.refused_version,
        token_prefix=prefix,
    )


async def _out(session: AsyncSession, node_id: uuid.UUID) -> NodeOut:
    [out] = [o for o in (await _outs(session))[1] if o.id == node_id]
    return out


def _created(out: NodeOut, token: str) -> NodeCreated:
    return NodeCreated.model_validate(out.model_dump() | {"token": token})


@router.get("")
async def list_nodes(session: SessionDep, authz: ManageNodes) -> NodeList:
    current, outs = await _outs(session)
    return NodeList(version=__version__, snapshot_version=current, nodes=outs)


@router.post("", status_code=201)
async def create_node(
    session: SessionDep, authz: ManageNodes, audit: AuditDep, body: NodeCreate
) -> NodeCreated:
    """Register a worker. Its join token is returned only here and on rotate."""
    node, token = await nodes.create_node(session, body.name)
    out = await _out(session, node.id)
    # The snapshot is NodeOut, so the token is never stored.
    await audit.record("node.create", "node", str(node.id), after=snapshot(out))
    await session.commit()
    return _created(out, token)


@router.post("/{id}/rotate", status_code=201)
async def rotate_node_token(
    session: SessionDep,
    authz: ManageNodes,
    audit: AuditDep,
    bus: ChangesDep,
    id: uuid.UUID,
    body: RotateIn,
) -> NodeCreated:
    """Give the node a new token. The old one keeps working for `overlap_seconds`; 0 revokes
    it at once."""
    node = await nodes.get_node(session, id, for_update=True)
    before = await _out(session, id)
    token = await nodes.rotate_token(session, node, timedelta(seconds=body.overlap_seconds))
    out = await _out(session, id)
    await audit.record("node.rotate", "node", str(id), before=snapshot(before), after=snapshot(out))
    await session.commit()
    await bus.publish_node(id)
    return _created(out, token)


@router.post("/{id}/revoke")
async def revoke_node(
    session: SessionDep, authz: ManageNodes, audit: AuditDep, bus: ChangesDep, id: uuid.UUID
) -> NodeOut:
    """Revoke the node and every token: the worker stops serving."""
    node = await nodes.get_node(session, id, for_update=True)
    before = await _out(session, id)
    await nodes.revoke_node(session, node)
    out = await _out(session, id)
    await audit.record("node.revoke", "node", str(id), before=snapshot(before), after=snapshot(out))
    await session.commit()
    await bus.publish_node(id)
    return out


@router.post("/{id}/drain")
async def drain_node(
    session: SessionDep, authz: ManageNodes, audit: AuditDep, bus: ChangesDep, id: uuid.UUID
) -> NodeOut:
    """Ask the worker to report not ready, so load balancers move traffic away."""
    node = await nodes.get_node(session, id, for_update=True)
    before = await _out(session, id)
    await nodes.drain_node(session, node)
    out = await _out(session, id)
    await audit.record("node.drain", "node", str(id), before=snapshot(before), after=snapshot(out))
    await session.commit()
    await bus.publish_node(id)
    return out


@router.post("/{id}/resume")
async def resume_node(
    session: SessionDep, authz: ManageNodes, audit: AuditDep, bus: ChangesDep, id: uuid.UUID
) -> NodeOut:
    node = await nodes.get_node(session, id, for_update=True)
    before = await _out(session, id)
    await nodes.resume_node(session, node)
    out = await _out(session, id)
    await audit.record("node.resume", "node", str(id), before=snapshot(before), after=snapshot(out))
    await session.commit()
    await bus.publish_node(id)
    return out


@router.delete("/{id}", status_code=204)
async def delete_node(
    session: SessionDep, authz: ManageNodes, audit: AuditDep, bus: ChangesDep, id: uuid.UUID
) -> Response:
    """Remove a revoked node."""
    node = await nodes.get_node(session, id, for_update=True)
    before = await _out(session, id)
    await nodes.delete_node(session, node)
    await audit.record("node.delete", "node", str(id), before=snapshot(before))
    await session.commit()
    await bus.publish_node(id)
    return Response(status_code=204)

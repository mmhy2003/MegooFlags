"""The worker-facing protocol (`/api/cluster/v1`): heartbeats and forwarded events.

Workers send their node token and their version. The routes need no user credentials
(PUBLIC_ROUTES); `require_node` checks the node instead.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select

from megooflags_api import __version__
from megooflags_api.db import SessionDep
from megooflags_api.errors import (
    ERROR_RESPONSES,
    incompatible_version,
    node_revoked,
    not_control_plane,
    unauthorized,
)
from megooflags_api.schemas.cluster import ClusterEventsIn, HeartbeatIn, HeartbeatOut
from megooflags_api.sdk_auth import bearer_token
from megooflags_api.services.cluster import nodes
from megooflags_api.services.cluster.compat import incompatibility
from megooflags_api.services.cluster.ingest import ingest_batch

router = APIRouter(prefix="/cluster/v1", tags=["cluster"], responses=ERROR_RESPONSES)

VERSION_HEADER = "X-MegooFlags-Version"


@dataclass(frozen=True, slots=True)
class NodeCaller:
    node_id: uuid.UUID
    token_id: uuid.UUID


async def require_node(request: Request, session: SessionDep) -> NodeCaller:
    """The calling worker: the role, then its token (401, 403), then its version (409)."""
    if request.app.state.settings.role != "control-plane":
        raise not_control_plane(
            "the control plane runs with MEGOOFLAGS_ROLE=standalone; "
            "set MEGOOFLAGS_ROLE=control-plane on it"
        )
    token = bearer_token(request)
    auth = None if token is None else await nodes.authenticate_token(session, token)
    if auth is None:
        raise unauthorized("missing or unknown node token")
    if not auth.active:
        raise node_revoked()
    worker = request.headers.get(VERSION_HEADER) or "unknown"
    reason = incompatibility(worker, __version__)
    if reason is not None:
        auth.node.refused_version = worker[:50]
        auth.node.refused_at = await session.scalar(select(func.now()))
        await session.commit()
        raise incompatible_version(worker, __version__, reason)
    return NodeCaller(node_id=auth.node.id, token_id=auth.token_id)


NodeDep = Annotated[NodeCaller, Depends(require_node)]


@router.post("/heartbeat")
async def heartbeat(
    request: Request, session: SessionDep, caller: NodeDep, body: HeartbeatIn
) -> HeartbeatOut:
    """Record the worker's state and answer with the state it should be in."""
    node = await nodes.get_node(session, caller.node_id, for_update=True)
    now = await session.scalar(select(func.now()))
    assert now is not None
    node.last_heartbeat_at = now
    node.version = body.version
    node.snapshot_version = body.snapshot_version
    if body.snapshot_age_seconds is not None:
        node.synced_at = now - timedelta(seconds=body.snapshot_age_seconds)
    node.sdk_streams = body.sdk_streams
    node.buffered_events = body.buffered_events
    node.address = None if request.client is None else request.client.host[:100]
    node.refused_version = None
    node.refused_at = None
    out = HeartbeatOut(state="active" if node.drain_requested_at is None else "draining")
    await session.commit()
    return out


@router.post(
    "/events",
    status_code=202,
    response_class=Response,
    responses={202: {"description": "The batch was stored, or was seen before."}},
)
async def send_events(
    request: Request, session: SessionDep, caller: NodeDep, body: ClusterEventsIn
) -> Response:
    """Store a batch of exposures and counts a worker received from its SDKs, once per
    `batch_id`. The data of a deleted environment is dropped."""
    await ingest_batch(
        session,
        caller.node_id,
        body,
        persist_context=request.app.state.settings.persist_context,
        now=datetime.now(UTC),
    )
    return Response(status_code=202)

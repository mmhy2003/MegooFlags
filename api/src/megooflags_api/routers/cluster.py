"""The worker-facing protocol (`/api/cluster/v1`): the snapshot stream, heartbeats and forwarded
events.

Workers send their node token and their version. The routes need no user credentials
(PUBLIC_ROUTES); `require_node` checks the node instead.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
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
from megooflags_api.schemas.cluster import ClusterEventsIn, HeartbeatIn, HeartbeatOut, StreamIn
from megooflags_api.sdk_auth import bearer_token
from megooflags_api.services.cluster import nodes
from megooflags_api.services.cluster.compat import incompatibility
from megooflags_api.services.cluster.ingest import ingest_batch
from megooflags_api.services.cluster.node_hub import REVOKED, NodeHub, StreamEvent
from megooflags_api.services.cluster.signing import SnapshotSigner

router = APIRouter(prefix="/cluster/v1", tags=["cluster"], responses=ERROR_RESPONSES)

VERSION_HEADER = "X-MegooFlags-Version"
RETRY_FRAME = b"retry: 3000\n\n"
PING_FRAME = b": ping\n\n"

logger = logging.getLogger(__name__)


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


def _address(request: Request) -> str | None:
    return None if request.client is None else request.client.host[:100]


async def _events(
    request: Request, caller: NodeCaller, state: str, body: StreamIn
) -> AsyncIterator[bytes]:
    settings = request.app.state.settings
    signer: SnapshotSigner = request.app.state.snapshot_signer
    hub: NodeHub = request.app.state.node_hub
    # Before the snapshots are read, so no change slips in between.
    stream = hub.subscribe(caller.node_id, caller.token_id, state)
    try:
        yield RETRY_FRAME
        # A revoke or drain committed before the subscription found no stream to tell.
        await hub.check(stream)
        if stream.revoked:
            yield REVOKED.frame()
            return
        hello = {
            "version": __version__,
            "public_key": signer.public_key,
            "persist_context": settings.persist_context,
            "state": stream.state,
            "ping_seconds": settings.sse_heartbeat_seconds,
        }
        yield StreamEvent("hello", json.dumps(hello).encode()).frame()
        try:
            snapshots = await hub.snapshots()
        except Exception:
            logger.exception("cannot send the snapshots to node %s", stream.node_id)
            return  # the worker reconnects with backoff
        held = body.versions if body.public_key == signer.public_key else {}
        sent: dict[uuid.UUID, int] = {}
        for env_id, snap in snapshots.items():
            sent[env_id] = snap.header.version
            if held.get(env_id) != snap.header.version:
                yield StreamEvent.put(snap).frame()
        for env_id in body.versions.keys() - snapshots.keys():
            yield StreamEvent.remove(env_id).frame()
        synced = {"snapshot_version": max(sent.values(), default=0)}
        yield StreamEvent("synced", json.dumps(synced).encode()).frame()
        while True:
            events = await stream.next(settings.sse_heartbeat_seconds)
            if events is None:
                yield PING_FRAME
                continue
            for event in events:
                if event.version is not None and event.environment_id is not None:
                    if event.version <= sent.get(event.environment_id, 0):
                        continue  # already sent in the first sync
                    sent[event.environment_id] = event.version
                yield event.frame()
                if event.event == "revoked":
                    return
    finally:
        hub.unsubscribe(stream)


@router.post(
    "/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Server-sent events: `hello`, a `put` (the signed snapshot, one line "
            "each for the signature, the header and the ruleset) for every environment the "
            "worker does not hold, `remove` for those gone, then `synced`. After that `put`, "
            "`remove`, `state`, `revoked` before the stream closes, and a `: ping` comment as "
            "heartbeat.",
        }
    },
)
async def stream_snapshots(
    request: Request, session: SessionDep, caller: NodeDep, body: StreamIn
) -> StreamingResponse:
    """Stream the signed environment snapshots to a worker, starting with those it lacks."""
    node = await nodes.get_node(session, caller.node_id, for_update=True)
    node.address = _address(request)
    node.refused_version = None
    node.refused_at = None
    state = "active" if node.drain_requested_at is None else "draining"
    await session.commit()
    return StreamingResponse(
        _events(request, caller, state, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    node.address = _address(request)
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

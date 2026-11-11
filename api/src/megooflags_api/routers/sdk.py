"""The SDK delivery API: ruleset download for server keys, remote evaluation for any key, a
change stream over server-sent events, and exposure events and evaluation counts."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from megooflags_api.db import SessionDep
from megooflags_api.errors import SDK_ERROR_RESPONSES
from megooflags_api.etags import canonical_json, derived_etag, etag_matches
from megooflags_api.evaluation import ErrorCode, evaluate_all
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.schemas.evaluation import EvaluateIn
from megooflags_api.schemas.events import EventsIn
from megooflags_api.schemas.sdk import SdkEvaluateOut, SdkEvaluationResult
from megooflags_api.sdk_auth import AnyKeyDep, ServerKeyDep, StreamKeyDep
from megooflags_api.services.events import (
    CountKey,
    EvaluationCounter,
    add_counts,
    limit_body_size,
    record_exposures,
    truncate_hour,
)
from megooflags_api.services.ruleset_cache import SDK_MAX_AGE, CachedRuleset, RulesetCache
from megooflags_api.services.sdk_keys import KeyResolver, SdkPrincipal
from megooflags_api.services.streams import Revoked, StreamHub

router = APIRouter(prefix="/sdk/v1", tags=["sdk"], responses=SDK_ERROR_RESPONSES)

IfNoneMatch = Annotated[str | None, Header()]

RETRY_FRAME = b"retry: 3000\n\n"
PING_FRAME = b": ping\n\n"
REVOKED_FRAME = b"event: revoked\ndata: {}\n\n"


async def _entry(request: Request, principal: SdkPrincipal) -> CachedRuleset:
    cache: RulesetCache = request.app.state.ruleset_cache
    async with request.app.state.sessionmaker() as session:
        return await cache.get_entry(session, principal.environment, max_age=SDK_MAX_AGE)


def _not_modified(etag: str) -> Response:
    return Response(status_code=304, headers={"ETag": etag})


@router.get(
    "/ruleset",
    response_class=Response,
    responses={
        200: {
            "content": {"application/json": {}},
            "description": "The ruleset (spec/ruleset.schema.json).",
        },
        304: {"description": "The ruleset matches `If-None-Match`."},
    },
)
async def download_ruleset(
    request: Request, principal: ServerKeyDep, if_none_match: IfNoneMatch = None
) -> Response:
    """The full ruleset of the key's environment, for SDKs that evaluate locally."""
    entry = await _entry(request, principal)
    if etag_matches(if_none_match, entry.etag):
        return _not_modified(entry.etag)
    return Response(
        entry.body,
        media_type="application/json",
        headers={"ETag": entry.etag, "Cache-Control": "no-cache"},
    )


@router.post(
    "/evaluate",
    response_model=SdkEvaluateOut,
    responses={304: {"description": "The results match `If-None-Match`."}},
)
async def evaluate_flags(
    request: Request, principal: AnyKeyDep, body: EvaluateIn, if_none_match: IfNoneMatch = None
) -> Response:
    """Evaluate flags for a context; results are sorted by key and carry no metadata for client
    keys, so rules are never exposed."""
    entry = await _entry(request, principal)
    flags = sorted(body.flags) if body.flags is not None else None
    etag = derived_etag(
        entry.etag, principal.kind, canonical_json(body.context), canonical_json(flags)
    )
    if etag_matches(if_none_match, etag):
        return _not_modified(etag)
    server = principal.kind == SdkKeyKind.SERVER
    resolutions = evaluate_all(entry.ruleset, body.context, body.flags)
    counter: EvaluationCounter = request.app.state.evaluation_counter
    counter.record(
        principal.environment.id,
        [
            (key, r.variant)
            for key, r in resolutions.items()
            if r.error_code != ErrorCode.FLAG_NOT_FOUND
        ],
    )
    results = []
    for key, r in resolutions.items():
        extra: dict[str, Any] = {"metadata": r.metadata} if server else {}
        results.append(
            SdkEvaluationResult(
                key=key,
                value=r.value,
                variant=r.variant,
                reason=r.reason,
                error_code=r.error_code,
                error_message=r.error_message,
                **extra,
            )
        )
    out = SdkEvaluateOut(results=results)
    return JSONResponse(out.model_dump(mode="json", exclude_unset=True), headers={"ETag": etag})


def _frame(principal: SdkPrincipal, entry: CachedRuleset) -> bytes:
    """Server keys get the whole ruleset; client keys only learn that it changed."""
    if principal.kind == SdkKeyKind.SERVER:
        return b"id: " + entry.etag.encode() + b"\nevent: put\ndata: " + entry.body + b"\n\n"
    data = json.dumps({"etag": entry.etag})
    return f"id: {entry.etag}\nevent: changed\ndata: {data}\n\n".encode()


async def _events(
    request: Request, principal: SdkPrincipal, last_event_id: str | None
) -> AsyncIterator[bytes]:
    hub: StreamHub = request.app.state.stream_hub
    heartbeat: float = request.app.state.settings.sse_heartbeat_seconds
    sub = hub.subscribe(principal)  # before the first read, so no change slips in between
    try:
        yield RETRY_FRAME
        entry = await _entry(request, principal)
        last = last_event_id
        if entry.etag != last:
            yield _frame(principal, entry)
            last = entry.etag
        while True:
            try:
                item = await asyncio.wait_for(sub.queue.get(), heartbeat)
            except TimeoutError:
                # Re-validate the key, so a revocation whose message was lost still closes.
                resolver: KeyResolver = request.app.state.key_resolver
                async with request.app.state.sessionmaker() as session:
                    current = await resolver.resolve_hash(session, principal.secret_hash)
                if current is None or current.inactive(datetime.now(UTC)):
                    yield REVOKED_FRAME
                    return
                yield PING_FRAME
                continue
            if isinstance(item, Revoked):
                yield REVOKED_FRAME
                return
            if item.etag != last:
                yield _frame(principal, item)
                last = item.etag
    finally:
        hub.unsubscribe(sub)


@router.get(
    "/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Server-sent events: `put` with the ruleset for server keys, `changed` "
            "with the new ETag for client keys, `revoked` before the stream closes, and a "
            "`: ping` comment as heartbeat.",
        }
    },
)
async def stream_changes(
    request: Request,
    principal: StreamKeyDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Stream ruleset changes of the key's environment. The first message is skipped when
    `Last-Event-ID` equals the current ETag."""
    return StreamingResponse(
        _events(request, principal, last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _events_key(request: Request, principal: AnyKeyDep) -> SdkPrincipal:
    """Authenticate before checking the body size, so anonymous callers always get 401."""
    await limit_body_size(request)
    return principal


@router.post(
    "/events",
    status_code=202,
    response_class=Response,
    responses={202: {"description": "The batch was stored."}},
)
async def send_events(
    request: Request,
    principal: Annotated[SdkPrincipal, Depends(_events_key)],
    body: EventsIn,
    session: SessionDep,
) -> Response:
    """Store exposures and add evaluation counts, at most 1000 of each and 1 MB per batch. The
    context is dropped unless `MEGOOFLAGS_PERSIST_CONTEXT` is enabled; unknown flags are kept."""
    env_id = principal.environment.id
    await record_exposures(
        session,
        env_id,
        body.exposures,
        persist_context=request.app.state.settings.persist_context,
        now=datetime.now(UTC),
    )
    counts: dict[CountKey, int] = {}
    for c in body.counts:
        key = (env_id, c.flag_key, c.variant or "", truncate_hour(c.hour))
        counts[key] = counts.get(key, 0) + c.count
    await add_counts(session, counts)
    await session.commit()
    return Response(status_code=202)

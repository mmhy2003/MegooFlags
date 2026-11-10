"""The OpenFeature Remote Evaluation Protocol (OFREP), so any OFREP provider can evaluate flags
with a server or client key. Client keys get no selection metadata, so rules are never exposed."""

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from megooflags_api.errors import SDK_ERROR_RESPONSES
from megooflags_api.etags import canonical_json, derived_etag, etag_matches
from megooflags_api.evaluation import ErrorCode, Resolution, evaluate, evaluate_all
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.schemas.ofrep import OfrepBulk, OfrepError, OfrepRequest, OfrepSuccess
from megooflags_api.sdk_auth import OfrepKeyDep
from megooflags_api.services.events import EvaluationCounter
from megooflags_api.services.ruleset_cache import SDK_MAX_AGE, CachedRuleset, RulesetCache
from megooflags_api.services.sdk_keys import SdkPrincipal

router = APIRouter(prefix="/ofrep/v1", tags=["ofrep"], responses=SDK_ERROR_RESPONSES)

IfNoneMatch = Annotated[str | None, Header()]


async def _entry(request: Request, principal: SdkPrincipal) -> CachedRuleset:
    cache: RulesetCache = request.app.state.ruleset_cache
    async with request.app.state.sessionmaker() as session:
        return await cache.get_entry(session, principal.environment, max_age=SDK_MAX_AGE)


def ofrep_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """OpenFeature's `targetingKey` is the attribute `key` (spec/evaluation.md §1)."""
    ctx = dict(ctx)
    if ctx.get("targetingKey") is not None:
        ctx["key"] = ctx["targetingKey"]
    return ctx


def _count(request: Request, principal: SdkPrincipal, results: dict[str, Resolution]) -> None:
    counter: EvaluationCounter = request.app.state.evaluation_counter
    counter.record(
        principal.environment.id,
        [(k, r.variant) for k, r in results.items() if r.error_code != ErrorCode.FLAG_NOT_FOUND],
    )


def _item(key: str, r: Resolution, principal: SdkPrincipal) -> OfrepSuccess | OfrepError:
    if r.error_code is not None:
        return OfrepError(key=key, errorCode=r.error_code, errorDetails=r.error_message)
    metadata = r.metadata if principal.kind == SdkKeyKind.SERVER else {}
    return OfrepSuccess(
        key=key, value=r.value, reason=r.reason, variant=r.variant, metadata=metadata
    )


def _dump(item: OfrepSuccess | OfrepError) -> dict[str, Any]:
    """Omit a null `variant` or `errorDetails`; a null `value` stays, as it is required."""
    data = item.model_dump(mode="json")
    for field in ("variant", "errorDetails"):
        if data.get(field, "") is None:
            del data[field]
    return data


@router.post(
    "/evaluate/flags/{key}",
    response_model=OfrepSuccess,
    responses={
        400: {"model": OfrepError, "description": "The evaluation failed."},
        404: {"model": OfrepError, "description": "The flag does not exist."},
    },
)
async def evaluate_flag(
    request: Request, principal: OfrepKeyDep, key: str, body: OfrepRequest
) -> Response:
    """Evaluate one flag. `PREREQUISITE_FAILED` is passed through as the reason."""
    entry = await _entry(request, principal)
    resolution = evaluate(entry.ruleset, key, ofrep_context(body.context), None)
    _count(request, principal, {key: resolution})
    item = _item(key, resolution, principal)
    if isinstance(item, OfrepError):
        status = 404 if item.errorCode == ErrorCode.FLAG_NOT_FOUND else 400
    else:
        status = 200
    return JSONResponse(_dump(item), status_code=status)


@router.post(
    "/evaluate/flags",
    response_model=OfrepBulk,
    responses={304: {"description": "The results match `If-None-Match`."}},
)
async def evaluate_flags(
    request: Request, principal: OfrepKeyDep, body: OfrepRequest, if_none_match: IfNoneMatch = None
) -> Response:
    """Evaluate every flag of the key's environment, sorted by key."""
    entry = await _entry(request, principal)
    ctx = ofrep_context(body.context)
    etag = derived_etag(entry.etag, principal.kind, canonical_json(ctx))
    if etag_matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})
    results = evaluate_all(entry.ruleset, ctx)
    _count(request, principal, results)
    flags = [_dump(_item(k, r, principal)) for k, r in results.items()]
    return JSONResponse({"flags": flags}, headers={"ETag": etag})

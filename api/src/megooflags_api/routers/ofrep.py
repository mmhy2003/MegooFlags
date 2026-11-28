"""The OpenFeature Remote Evaluation Protocol (OFREP), so any OFREP provider can evaluate flags
with a server or client key. Client keys get no selection metadata, so rules are never exposed."""

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response

from megooflags_api.errors import SDK_ERROR_RESPONSES
from megooflags_api.etags import canonical_json, derived_etag, etag_matches
from megooflags_api.evaluation import ErrorCode, Resolution, evaluate, evaluate_all
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.responses import FastJSONResponse
from megooflags_api.schemas.ofrep import OfrepBulk, OfrepError, OfrepRequest, OfrepSuccess
from megooflags_api.sdk_auth import OfrepKeyDep
from megooflags_api.services.delivery import get_delivery
from megooflags_api.services.ruleset_cache import CachedRuleset
from megooflags_api.services.sdk_keys import SdkPrincipal

router = APIRouter(prefix="/ofrep/v1", tags=["ofrep"], responses=SDK_ERROR_RESPONSES)

IfNoneMatch = Annotated[str | None, Header()]


async def _entry(request: Request, principal: SdkPrincipal) -> CachedRuleset:
    return await get_delivery(request).rulesets.get_entry(principal.environment)


def ofrep_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """OpenFeature's `targetingKey` is the attribute `key` (spec/evaluation.md §1)."""
    ctx = dict(ctx)
    if ctx.get("targetingKey") is not None:
        ctx["key"] = ctx["targetingKey"]
    return ctx


def _count(request: Request, principal: SdkPrincipal, results: dict[str, Resolution]) -> None:
    get_delivery(request).events.record_counts(
        principal.environment.id,
        [(k, r.variant) for k, r in results.items() if r.error_code != ErrorCode.FLAG_NOT_FOUND],
    )


def _item(key: str, r: Resolution, server: bool) -> dict[str, Any]:
    """One OFREP result; a null `variant` or `errorDetails` is omitted, a null `value` stays."""
    if r.error_code is not None:
        error: dict[str, Any] = {"key": key, "errorCode": r.error_code}
        if r.error_message is not None:
            error["errorDetails"] = r.error_message
        return error
    item: dict[str, Any] = {"key": key, "value": r.value, "reason": r.reason}
    if r.variant is not None:
        item["variant"] = r.variant
    item["metadata"] = r.metadata if server else {}
    return item


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
    item = _item(key, resolution, principal.kind == SdkKeyKind.SERVER)
    if resolution.error_code is None:
        status = 200
    else:
        status = 404 if resolution.error_code == ErrorCode.FLAG_NOT_FOUND else 400
    return FastJSONResponse(item, status_code=status)


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
    results = evaluate_all(entry.ruleset, ctx, sorted_keys=entry.sorted_keys)
    _count(request, principal, results)
    server = principal.kind == SdkKeyKind.SERVER
    flags = [_item(k, r, server) for k, r in results.items()]
    return FastJSONResponse({"flags": flags}, headers={"ETag": etag})

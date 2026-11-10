"""The SDK delivery API: ruleset download for server keys, remote evaluation for any key."""

from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from megooflags_api.errors import SDK_ERROR_RESPONSES
from megooflags_api.etags import canonical_json, derived_etag, etag_matches
from megooflags_api.evaluation import evaluate_all
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.schemas.evaluation import EvaluateIn
from megooflags_api.schemas.sdk import SdkEvaluateOut, SdkEvaluationResult
from megooflags_api.sdk_auth import AnyKeyDep, ServerKeyDep
from megooflags_api.services.ruleset_cache import SDK_MAX_AGE, CachedRuleset, RulesetCache
from megooflags_api.services.sdk_keys import SdkPrincipal

router = APIRouter(prefix="/sdk/v1", tags=["sdk"], responses=SDK_ERROR_RESPONSES)

IfNoneMatch = Annotated[str | None, Header()]


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
    results = []
    for key, r in evaluate_all(entry.ruleset, body.context, body.flags).items():
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

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.etags import etag_matches
from megooflags_api.evaluation import evaluate_all
from megooflags_api.schemas.evaluation import EvaluateIn, EvaluateOut, EvaluationResult
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.ruleset_cache import RulesetCache

router = APIRouter(tags=["evaluation"], responses=ERROR_RESPONSES)

Evaluate = Annotated[Authz, Depends(require(Permission.EVALUATE))]
Read = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]
IfNoneMatch = Annotated[str | None, Header()]


@router.post("/projects/{project}/environments/{env}/evaluate")
async def evaluate_flags(
    session: SessionDep,
    authz: Evaluate,
    request: Request,
    project: str,
    env: str,
    body: EvaluateIn,
) -> EvaluateOut:
    """Evaluate flags for a context exactly as SDKs do; results are sorted by key."""
    env_row = authz.environment
    assert env_row is not None
    cache: RulesetCache = request.app.state.ruleset_cache
    ruleset = await cache.get(session, EnvRef.of(env_row))
    results = evaluate_all(ruleset, body.context, body.flags)
    return EvaluateOut(
        results=[
            EvaluationResult(
                key=key,
                value=r.value,
                variant=r.variant,
                reason=r.reason,
                error_code=r.error_code,
                error_message=r.error_message,
                metadata=r.metadata,
            )
            for key, r in results.items()
        ]
    )


@router.get(
    "/projects/{project}/environments/{env}/ruleset",
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
    session: SessionDep,
    authz: Read,
    request: Request,
    project: str,
    env: str,
    if_none_match: IfNoneMatch = None,
) -> Response:
    """The ruleset of the environment, byte for byte what `/sdk/v1/ruleset` serves, for API
    tokens: `megooflags ruleset pull` saves it as a bootstrap file for the SDKs' offline mode."""
    env_row = authz.environment
    assert env_row is not None
    cache: RulesetCache = request.app.state.ruleset_cache
    entry = await cache.get_entry(session, EnvRef.of(env_row))
    if etag_matches(if_none_match, entry.etag):
        return Response(status_code=304, headers={"ETag": entry.etag})
    return Response(
        entry.body,
        media_type="application/json",
        headers={"ETag": entry.etag, "Cache-Control": "no-cache"},
    )

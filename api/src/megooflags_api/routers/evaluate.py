from typing import Annotated

from fastapi import APIRouter, Depends, Request

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.evaluation import evaluate_all
from megooflags_api.schemas.evaluation import EvaluateIn, EvaluateOut, EvaluationResult
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.ruleset_cache import RulesetCache

router = APIRouter(tags=["evaluation"], responses=ERROR_RESPONSES)

Evaluate = Annotated[Authz, Depends(require(Permission.EVALUATE))]


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

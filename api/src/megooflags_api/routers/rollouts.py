from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models import Environment, Project
from megooflags_api.schemas.rollouts import ProgressiveRolloutIn, ProgressiveRolloutOut, RolloutView
from megooflags_api.services import rollouts
from megooflags_api.services.audit import Actor, AuditDep
from megooflags_api.services.changes import ChangesDep
from megooflags_api.services.lookup import get_flag, get_flag_environment

router = APIRouter(tags=["rollouts"], responses=ERROR_RESPONSES)

ReadProject = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]
WriteConfig = Annotated[Authz, Depends(require(Permission.FLAG_CONFIG_WRITE))]

ROLLOUT = "/projects/{project}/flags/{flag}/environments/{env}/rollout"


@router.post(ROLLOUT, status_code=201)
async def start_rollout(
    session: SessionDep,
    authz: WriteConfig,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    env: str,
    body: ProgressiveRolloutIn,
) -> ProgressiveRolloutOut:
    """Start a progressive rollout of the fallthrough and apply its first step at once."""
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    row = await rollouts.start(
        session, audit, flag_row, env_row, fe, body, Actor.of(authz.principal), datetime.now(UTC)
    )
    out = await rollouts.to_out(session, row, env_row.key)
    await session.commit()
    await bus.publish(project_row.id)
    return out


@router.get(ROLLOUT)
async def read_rollout(
    session: SessionDep, authz: ReadProject, project: str, flag: str, env: str
) -> RolloutView:
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row)
    return await rollouts.view(session, fe, env_row.key)


@router.post(f"{ROLLOUT}/pause")
async def pause_rollout(
    session: SessionDep, authz: WriteConfig, audit: AuditDep, project: str, flag: str, env: str
) -> ProgressiveRolloutOut:
    """Stop the clock of the active rollout; the traffic split stays as it is."""
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    row = await rollouts.pause(session, audit, flag_row, env_row, fe, datetime.now(UTC))
    out = await rollouts.to_out(session, row, env_row.key)
    await session.commit()
    return out


@router.post(f"{ROLLOUT}/resume")
async def resume_rollout(
    session: SessionDep, authz: WriteConfig, audit: AuditDep, project: str, flag: str, env: str
) -> ProgressiveRolloutOut:
    """Restart the clock of a paused rollout with the wait it had left."""
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    row = await rollouts.resume(session, audit, flag_row, env_row, fe, datetime.now(UTC))
    out = await rollouts.to_out(session, row, env_row.key)
    await session.commit()
    return out


@router.post(f"{ROLLOUT}/abort")
async def abort_rollout(
    session: SessionDep,
    authz: WriteConfig,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    env: str,
) -> ProgressiveRolloutOut:
    """Stop the rollout and serve the "from" variation to all of the fallthrough again."""
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    row = await rollouts.abort(session, audit, flag_row, env_row, fe, datetime.now(UTC))
    out = await rollouts.to_out(session, row, env_row.key)
    await session.commit()
    await bus.publish(project_row.id)
    return out


def _project(authz: Authz) -> Project:
    assert authz.project is not None  # every route here has {project}
    return authz.project


def _environment(authz: Authz) -> Environment:
    assert authz.environment is not None  # every route here has {env}
    return authz.environment

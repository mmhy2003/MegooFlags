import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models import Environment, Project
from megooflags_api.models.enums import ScheduledAction
from megooflags_api.schemas.schedules import ScheduleIn, ScheduleOut
from megooflags_api.services import approvals, schedules
from megooflags_api.services.audit import Actor, AuditDep
from megooflags_api.services.lookup import get_flag, get_flag_environment

router = APIRouter(tags=["schedules"], responses=ERROR_RESPONSES)

ReadProject = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]

SCHEDULES = "/projects/{project}/flags/{flag}/environments/{env}/schedules"


@router.get(SCHEDULES)
async def list_schedules(
    session: SessionDep, authz: ReadProject, project: str, flag: str, env: str
) -> list[ScheduleOut]:
    """The flag's scheduled changes in the environment, latest `run_at` first (at most 100)."""
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row)
    return await schedules.list_for(session, fe, env_row.key)


@router.post(SCHEDULES, status_code=201)
async def create_schedule(
    session: SessionDep,
    authz: ReadProject,
    audit: AuditDep,
    project: str,
    flag: str,
    env: str,
    body: ScheduleIn,
) -> ScheduleOut:
    """Schedule a change; enable and disable need flag.toggle, set_rollout flag.config.write."""
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    authz.check(_permission(body.action), project_row.id, env_row.id)
    approvals.ensure_direct_write(env_row)
    row = await schedules.create(
        session, audit, flag_row, env_row, fe, body, Actor.of(authz.principal), datetime.now(UTC)
    )
    out = schedules.schedule_out(row, env_row.key)
    await session.commit()
    return out


@router.delete(f"{SCHEDULES}/{{id}}")
async def cancel_schedule(
    session: SessionDep,
    authz: ReadProject,
    audit: AuditDep,
    project: str,
    flag: str,
    env: str,
    id: uuid.UUID,
) -> ScheduleOut:
    """Cancel a pending schedule; it needs the permission of the schedule's action."""
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row)
    row = await schedules.get(session, fe, id)
    authz.check(_permission(row.action), project_row.id, env_row.id)
    out = await schedules.cancel(session, audit, flag_row, env_row, fe, id)
    await session.commit()
    return out


def _permission(action: ScheduledAction) -> Permission:
    if action == ScheduledAction.SET_ROLLOUT:
        return Permission.FLAG_CONFIG_WRITE
    return Permission.FLAG_TOGGLE


def _project(authz: Authz) -> Project:
    assert authz.project is not None  # every route here has {project}
    return authz.project


def _environment(authz: Authz) -> Environment:
    assert authz.environment is not None  # every route here has {env}
    return authz.environment

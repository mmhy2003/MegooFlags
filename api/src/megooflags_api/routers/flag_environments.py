from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES, not_found, version_conflict
from megooflags_api.models import Environment, FlagEnvironment, Project
from megooflags_api.schemas.common import FlagEnvironmentState
from megooflags_api.schemas.targeting import CopyIn, FlagConfigIn, FlagConfigOut, ToggleIn
from megooflags_api.services import approvals, rollouts, targeting
from megooflags_api.services.audit import AuditDep, record_flag_change
from megooflags_api.services.changes import ChangesDep
from megooflags_api.services.flags import environment_states
from megooflags_api.services.lookup import get_environment, get_flag, get_flag_environment

router = APIRouter(tags=["flag-environments"], responses=ERROR_RESPONSES)

ReadProject = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]
WriteConfig = Annotated[Authz, Depends(require(Permission.FLAG_CONFIG_WRITE))]
Toggle = Annotated[Authz, Depends(require(Permission.FLAG_TOGGLE))]


@router.get("/projects/{project}/flags/{flag}/environments")
async def list_flag_environments(
    session: SessionDep, authz: ReadProject, project: str, flag: str
) -> list[FlagEnvironmentState]:
    """The flag's state in each environment the caller can see."""
    project_row = _project(authz)
    row = await get_flag(session, project_row, flag)
    visible = authz.authorizer.visible_environment_ids(project_row.id)
    return (await environment_states(session, [row.id], visible))[row.id]


@router.get("/projects/{project}/flags/{flag}/environments/{env}")
async def read_flag_config(
    session: SessionDep, authz: ReadProject, project: str, flag: str, env: str
) -> FlagConfigOut:
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row)
    return await targeting.load_config(session, flag_row, fe, env_row.key)


@router.put("/projects/{project}/flags/{flag}/environments/{env}")
async def replace_flag_config(
    session: SessionDep,
    authz: WriteConfig,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    env: str,
    body: FlagConfigIn,
) -> FlagConfigOut:
    """Replace the whole config; `version` must match the current one, or 409."""
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    approvals.ensure_direct_write(env_row)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    before = await targeting.load_config(session, flag_row, fe, env_row.key)
    await targeting.replace_config(session, flag_row, fe, body)
    out = await targeting.load_config(session, flag_row, fe, env_row.key)
    await record_flag_change(audit, "flag.config.update", flag_row, env_row, before, out)
    await session.commit()
    await bus.publish(project_row.id)
    return out


@router.post("/projects/{project}/flags/{flag}/environments/{env}/toggle")
async def toggle_flag(
    session: SessionDep,
    authz: Toggle,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    env: str,
    body: ToggleIn,
) -> FlagEnvironmentState:
    """Turn the flag on or off; when `version` is set it must match the current one, or 409."""
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    approvals.ensure_direct_write(env_row)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    if body.version is not None and body.version != fe.version:
        raise version_conflict(fe.version)
    before = targeting.state_of(fe, env_row.key)
    await targeting.set_enabled(session, fe, body.enabled)
    await record_flag_change(
        audit, "flag.toggle", flag_row, env_row, before, targeting.state_of(fe, env_row.key)
    )
    await session.commit()
    await bus.publish(project_row.id)
    return targeting.state_of(fe, env_row.key)


@router.post("/projects/{project}/flags/{flag}/environments/{env}/kill")
async def kill_flag(
    session: SessionDep,
    authz: Toggle,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    env: str,
) -> FlagEnvironmentState:
    """Kill switch: turn the flag off, whatever the version; targeting is left as is.

    The delivery service pushes the change to SDKs (F-6).
    """
    project_row, env_row = _project(authz), _environment(authz)
    flag_row = await get_flag(session, project_row, flag)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    before = targeting.state_of(fe, env_row.key)
    await targeting.set_enabled(session, fe, False)
    await rollouts.pause_live(
        session, audit, flag_row, env_row, fe, datetime.now(UTC), reason="kill switch"
    )
    await record_flag_change(
        audit, "flag.kill", flag_row, env_row, before, targeting.state_of(fe, env_row.key)
    )
    await session.commit()
    await bus.publish(project_row.id)
    return targeting.state_of(fe, env_row.key)


@router.post("/projects/{project}/flags/{flag}/copy")
async def copy_flag_config(
    session: SessionDep,
    authz: ReadProject,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    body: CopyIn,
) -> list[FlagEnvironmentState]:
    """Copy targets, rules, rollouts and prerequisites from one environment to others.

    The on/off state is copied only with `include_enabled`. Returns the target environments' new
    state, in environment order. Environments hidden from the caller are 404, and the caller
    needs flag.config.write on every target (else 403).
    """
    project_row = _project(authz)
    flag_row = await get_flag(session, project_row, flag)
    source = await _visible_environment(session, authz, body.source)
    targets = [await _visible_environment(session, authz, key) for key in body.to]
    for target in targets:
        authz.check(Permission.FLAG_CONFIG_WRITE, project_row.id, target.id)
        approvals.ensure_direct_write(target)
    # Locked in id order before copy_config's prerequisite locks, like a PUT, for the snapshots.
    rows = await session.scalars(
        select(FlagEnvironment)
        .where(
            FlagEnvironment.flag_id == flag_row.id,
            FlagEnvironment.environment_id.in_([target.id for target in targets]),
        )
        .order_by(FlagEnvironment.id)
        .with_for_update()
    )
    fes = {fe.environment_id: fe for fe in rows.all()}
    before = {
        target.id: await targeting.load_config(session, flag_row, fes[target.id], target.key)
        for target in targets
    }
    await targeting.copy_config(session, flag_row, source, targets, body.include_enabled)
    for target in targets:
        after = await targeting.load_config(session, flag_row, fes[target.id], target.key)
        await record_flag_change(audit, "flag.copy", flag_row, target, before[target.id], after)
    await session.commit()
    await bus.publish(project_row.id)
    states = (await environment_states(session, [flag_row.id]))[flag_row.id]
    return [state for state in states if state.environment in body.to]


def _project(authz: Authz) -> Project:
    assert authz.project is not None  # every route here has {project}
    return authz.project


def _environment(authz: Authz) -> Environment:
    assert authz.environment is not None  # the route has {env}
    return authz.environment


async def _visible_environment(session: AsyncSession, authz: Authz, key: str) -> Environment:
    """The project's environment `key`; 404 when it does not exist or is hidden from the caller."""
    project_row = _project(authz)
    env_row = await get_environment(session, project_row, key)
    if not authz.authorizer.can_see_environment(project_row.id, env_row.id):
        raise not_found("environment", key)
    return env_row

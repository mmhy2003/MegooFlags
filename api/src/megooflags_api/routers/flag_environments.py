from fastapi import APIRouter

from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES, version_conflict
from megooflags_api.models import FlagEnvironment
from megooflags_api.schemas.common import FlagEnvironmentState
from megooflags_api.schemas.targeting import FlagConfigIn, FlagConfigOut, ToggleIn
from megooflags_api.services import targeting
from megooflags_api.services.flags import environment_states
from megooflags_api.services.lookup import (
    get_environment,
    get_flag,
    get_flag_environment,
    get_project,
)

router = APIRouter(tags=["flag-environments"], responses=ERROR_RESPONSES)


@router.get("/projects/{project}/flags/{flag}/environments")
async def list_flag_environments(
    session: SessionDep, project: str, flag: str
) -> list[FlagEnvironmentState]:
    row = await get_flag(session, await get_project(session, project), flag)
    return (await environment_states(session, [row.id]))[row.id]


@router.get("/projects/{project}/flags/{flag}/environments/{environment}")
async def read_flag_config(
    session: SessionDep, project: str, flag: str, environment: str
) -> FlagConfigOut:
    project_row = await get_project(session, project)
    flag_row = await get_flag(session, project_row, flag)
    env_row = await get_environment(session, project_row, environment)
    fe = await get_flag_environment(session, flag_row, env_row)
    return await targeting.load_config(session, flag_row, fe, env_row.key)


@router.put("/projects/{project}/flags/{flag}/environments/{environment}")
async def replace_flag_config(
    session: SessionDep, project: str, flag: str, environment: str, body: FlagConfigIn
) -> FlagConfigOut:
    """Replace the whole config; `version` must match the current one, or 409."""
    project_row = await get_project(session, project)
    flag_row = await get_flag(session, project_row, flag)
    env_row = await get_environment(session, project_row, environment)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    await targeting.replace_config(session, flag_row, fe, body)
    await session.commit()
    return await targeting.load_config(session, flag_row, fe, env_row.key)


@router.post("/projects/{project}/flags/{flag}/environments/{environment}/toggle")
async def toggle_flag(
    session: SessionDep, project: str, flag: str, environment: str, body: ToggleIn
) -> FlagEnvironmentState:
    """Turn the flag on or off; when `version` is set it must match the current one, or 409."""
    project_row = await get_project(session, project)
    flag_row = await get_flag(session, project_row, flag)
    env_row = await get_environment(session, project_row, environment)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    if body.version is not None and body.version != fe.version:
        raise version_conflict(fe.version)
    await targeting.set_enabled(session, fe, body.enabled)
    await session.commit()
    return _state(fe, env_row.key)


@router.post("/projects/{project}/flags/{flag}/environments/{environment}/kill")
async def kill_flag(
    session: SessionDep, project: str, flag: str, environment: str
) -> FlagEnvironmentState:
    """Kill switch: turn the flag off, whatever the version; targeting is left as is.

    The delivery service pushes the change to SDKs (F-6).
    """
    project_row = await get_project(session, project)
    flag_row = await get_flag(session, project_row, flag)
    env_row = await get_environment(session, project_row, environment)
    fe = await get_flag_environment(session, flag_row, env_row, lock=True)
    await targeting.set_enabled(session, fe, False)
    await session.commit()
    return _state(fe, env_row.key)


def _state(fe: FlagEnvironment, env_key: str) -> FlagEnvironmentState:
    return FlagEnvironmentState(
        environment=env_key, enabled=fe.enabled, version=fe.version, updated_at=fe.updated_at
    )

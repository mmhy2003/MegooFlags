from fastapi import APIRouter

from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.schemas.common import FlagEnvironmentState
from megooflags_api.schemas.targeting import FlagConfigIn, FlagConfigOut
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

from fastapi import APIRouter, Response

from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.schemas.projects import EnvironmentCreate, EnvironmentOut, EnvironmentUpdate
from megooflags_api.services import environments
from megooflags_api.services.changes import ChangesDep
from megooflags_api.services.lookup import get_environment, get_project

router = APIRouter(tags=["environments"], responses=ERROR_RESPONSES)


@router.get("/projects/{project}/environments")
async def list_environments(session: SessionDep, project: str) -> list[EnvironmentOut]:
    rows = await environments.list_environments(session, await get_project(session, project))
    return [EnvironmentOut.model_validate(e, from_attributes=True) for e in rows]


@router.post("/projects/{project}/environments", status_code=201)
async def create_environment(
    session: SessionDep, bus: ChangesDep, project: str, body: EnvironmentCreate
) -> EnvironmentOut:
    project_row = await get_project(session, project)
    row = await environments.create_environment(session, project_row, body)
    out = EnvironmentOut.model_validate(row, from_attributes=True)
    await session.commit()
    await bus.publish(project_row.id)
    return out


@router.get("/projects/{project}/environments/{env}")
async def read_environment(session: SessionDep, project: str, env: str) -> EnvironmentOut:
    row = await get_environment(session, await get_project(session, project), env)
    return EnvironmentOut.model_validate(row, from_attributes=True)


@router.patch("/projects/{project}/environments/{env}")
async def update_environment(
    session: SessionDep, bus: ChangesDep, project: str, env: str, body: EnvironmentUpdate
) -> EnvironmentOut:
    row = await get_environment(session, await get_project(session, project), env)
    await environments.update_environment(session, row, body)
    out = EnvironmentOut.model_validate(row, from_attributes=True)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.delete("/projects/{project}/environments/{env}", status_code=204)
async def delete_environment(
    session: SessionDep, bus: ChangesDep, project: str, env: str
) -> Response:
    row = await get_environment(session, await get_project(session, project), env)
    project_id = row.project_id
    await environments.delete_environment(session, row)
    await session.commit()
    await bus.publish(project_id)
    return Response(status_code=204)

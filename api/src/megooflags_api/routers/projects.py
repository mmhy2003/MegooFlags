from fastapi import APIRouter, Query, Response

from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.projects import (
    ProjectCreate,
    ProjectOut,
    ProjectSummary,
    ProjectUpdate,
)
from megooflags_api.services import projects
from megooflags_api.services.lookup import get_project

router = APIRouter(tags=["projects"], responses=ERROR_RESPONSES)


@router.get("/projects")
async def list_projects(
    session: SessionDep, cursor: str | None = None, limit: int = Query(50, ge=1, le=500)
) -> Page[ProjectSummary]:
    items, next_cursor = await projects.list_projects(session, cursor, limit)
    return Page[ProjectSummary](items=items, next_cursor=next_cursor)


@router.post("/projects", status_code=201)
async def create_project(session: SessionDep, body: ProjectCreate) -> ProjectOut:
    project = await projects.create_project(session, body)
    out = await projects.project_out(session, project)
    await session.commit()
    return out


@router.get("/projects/{project}")
async def read_project(session: SessionDep, project: str) -> ProjectOut:
    return await projects.project_out(session, await get_project(session, project))


@router.patch("/projects/{project}")
async def update_project(session: SessionDep, project: str, body: ProjectUpdate) -> ProjectOut:
    row = await get_project(session, project)
    await projects.update_project(session, row, body)
    out = await projects.project_out(session, row)
    await session.commit()
    return out


@router.delete("/projects/{project}", status_code=204)
async def delete_project(session: SessionDep, project: str) -> Response:
    await projects.delete_project(session, await get_project(session, project))
    await session.commit()
    return Response(status_code=204)

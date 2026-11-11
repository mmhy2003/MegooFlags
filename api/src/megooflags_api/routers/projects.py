import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, authenticated, require
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
from megooflags_api.services.audit import AuditDep, snapshot
from megooflags_api.services.changes import ChangesDep

router = APIRouter(tags=["projects"], responses=ERROR_RESPONSES)

Authenticated = Annotated[Authz, Depends(authenticated())]
ReadProject = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]
ManageProject = Annotated[Authz, Depends(require(Permission.PROJECT_MANAGE))]
CreateProject = Annotated[Authz, Depends(require(Permission.PROJECT_CREATE))]


@router.get("/projects")
async def list_projects(
    session: SessionDep,
    authz: Authenticated,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[ProjectSummary]:
    items, next_cursor = await projects.list_projects(
        session, cursor, limit, authz.authorizer.visible_project_ids()
    )
    return Page[ProjectSummary](items=items, next_cursor=next_cursor)


@router.post("/projects", status_code=201)
async def create_project(
    session: SessionDep, authz: CreateProject, audit: AuditDep, bus: ChangesDep, body: ProjectCreate
) -> ProjectOut:
    project = await projects.create_project(session, body)
    out = await projects.project_out(session, project)
    await audit.record(
        "project.create", "project", project.key, project_id=project.id, after=snapshot(out)
    )
    await session.commit()
    await bus.publish(project.id)
    return out


@router.get("/projects/{project}")
async def read_project(session: SessionDep, authz: ReadProject, project: str) -> ProjectOut:
    assert authz.project is not None
    return await projects.project_out(session, authz.project, _visible_envs(authz))


@router.patch("/projects/{project}")
async def update_project(
    session: SessionDep,
    authz: ManageProject,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    body: ProjectUpdate,
) -> ProjectOut:
    row = authz.project
    assert row is not None
    before = await projects.project_out(session, row, _visible_envs(authz))
    await projects.update_project(session, row, body)
    out = await projects.project_out(session, row, _visible_envs(authz))
    await audit.record(
        "project.update",
        "project",
        row.key,
        project_id=row.id,
        before=snapshot(before),
        after=snapshot(out),
    )
    await session.commit()
    await bus.publish(row.id)
    return out


@router.delete("/projects/{project}", status_code=204)
async def delete_project(
    session: SessionDep, authz: ManageProject, audit: AuditDep, bus: ChangesDep, project: str
) -> Response:
    row = authz.project
    assert row is not None
    project_id = row.id
    before = await projects.project_out(session, row, _visible_envs(authz))
    await audit.record(
        "project.delete", "project", row.key, project_id=project_id, before=snapshot(before)
    )
    await projects.delete_project(session, row)
    await session.commit()
    await bus.publish(project_id)
    return Response(status_code=204)


def _visible_envs(authz: Authz) -> set[uuid.UUID] | None:
    assert authz.project is not None
    return authz.authorizer.visible_environment_ids(authz.project.id)

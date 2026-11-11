from typing import Annotated

from fastapi import APIRouter, Depends, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.schemas.projects import EnvironmentCreate, EnvironmentOut, EnvironmentUpdate
from megooflags_api.services import environments
from megooflags_api.services.audit import AuditDep, snapshot
from megooflags_api.services.changes import ChangesDep

router = APIRouter(tags=["environments"], responses=ERROR_RESPONSES)

ReadProject = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]
ManageEnvironments = Annotated[Authz, Depends(require(Permission.ENVIRONMENT_MANAGE))]


@router.get("/projects/{project}/environments")
async def list_environments(
    session: SessionDep, authz: ReadProject, project: str
) -> list[EnvironmentOut]:
    assert authz.project is not None
    visible = authz.authorizer.visible_environment_ids(authz.project.id)
    rows = await environments.list_environments(session, authz.project, visible)
    return [EnvironmentOut.model_validate(e, from_attributes=True) for e in rows]


@router.post("/projects/{project}/environments", status_code=201)
async def create_environment(
    session: SessionDep,
    authz: ManageEnvironments,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    body: EnvironmentCreate,
) -> EnvironmentOut:
    assert authz.project is not None
    row = await environments.create_environment(session, authz.project, body)
    out = EnvironmentOut.model_validate(row, from_attributes=True)
    await audit.record(
        "environment.create",
        "environment",
        row.key,
        project_id=row.project_id,
        environment_id=row.id,
        after=snapshot(out),
    )
    await session.commit()
    await bus.publish(authz.project.id)
    return out


@router.get("/projects/{project}/environments/{env}")
async def read_environment(
    session: SessionDep, authz: ReadProject, project: str, env: str
) -> EnvironmentOut:
    return EnvironmentOut.model_validate(authz.environment, from_attributes=True)


@router.patch("/projects/{project}/environments/{env}")
async def update_environment(
    session: SessionDep,
    authz: ManageEnvironments,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    env: str,
    body: EnvironmentUpdate,
) -> EnvironmentOut:
    row = authz.environment
    assert row is not None
    before = EnvironmentOut.model_validate(row, from_attributes=True)
    await environments.update_environment(session, row, body)
    out = EnvironmentOut.model_validate(row, from_attributes=True)
    await audit.record(
        "environment.update",
        "environment",
        row.key,
        project_id=row.project_id,
        environment_id=row.id,
        before=snapshot(before),
        after=snapshot(out),
    )
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.delete("/projects/{project}/environments/{env}", status_code=204)
async def delete_environment(
    session: SessionDep,
    authz: ManageEnvironments,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    env: str,
) -> Response:
    row = authz.environment
    assert row is not None
    project_id = row.project_id
    await audit.record(
        "environment.delete",
        "environment",
        row.key,
        project_id=project_id,
        environment_id=row.id,
        before=snapshot(EnvironmentOut.model_validate(row, from_attributes=True)),
    )
    await environments.delete_environment(session, row)
    await session.commit()
    await bus.publish(project_id)
    return Response(status_code=204)

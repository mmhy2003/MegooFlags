"""Project CRUD. A new project gets its environments in the same transaction."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import key_conflict, validation_error
from megooflags_api.models import Environment, Project
from megooflags_api.schemas.projects import (
    EnvironmentCreate,
    EnvironmentOut,
    ProjectCreate,
    ProjectOut,
    ProjectSummary,
    ProjectUpdate,
)
from megooflags_api.services.pagination import decode_cursor, page_slice

DEFAULT_ENVIRONMENTS = [
    EnvironmentCreate(key="development", name="Development", sort_order=0),
    EnvironmentCreate(key="staging", name="Staging", sort_order=1),
    EnvironmentCreate(key="production", name="Production", sort_order=2),
]


async def create_project(session: AsyncSession, body: ProjectCreate) -> Project:
    environments = DEFAULT_ENVIRONMENTS if body.environments is None else body.environments
    seen: set[str] = set()
    for i, env in enumerate(environments):
        if env.key in seen:
            raise validation_error(
                f"environment '{env.key}' is listed twice", f"environments[{i}].key"
            )
        seen.add(env.key)

    project = Project(key=body.key, name=body.name, description=body.description)
    session.add(project)
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("project", body.key) from None
        raise
    session.add_all(
        Environment(
            project_id=project.id,
            key=env.key,
            name=env.name,
            description=env.description,
            sort_order=i if env.sort_order is None else env.sort_order,
        )
        for i, env in enumerate(environments)
    )
    await session.flush()
    return project


async def list_projects(
    session: AsyncSession, cursor: str | None, limit: int, visible: set[uuid.UUID] | None = None
) -> tuple[list[ProjectSummary], str | None]:
    """A page of projects; `visible` limits it to those ids (None means all)."""
    query = select(Project).order_by(Project.key).limit(limit + 1)
    if visible is not None:
        query = query.where(Project.id.in_(visible))
    if cursor is not None:
        query = query.where(Project.key > decode_cursor(cursor))
    rows = list(await session.scalars(query))
    page, next_cursor = page_slice(rows, limit, lambda p: p.key)
    return [ProjectSummary.model_validate(p, from_attributes=True) for p in page], next_cursor


async def project_out(
    session: AsyncSession, project: Project, visible: set[uuid.UUID] | None = None
) -> ProjectOut:
    """The project with its environments; `visible` limits them to those ids (None means all)."""
    query = (
        select(Environment)
        .where(Environment.project_id == project.id)
        .order_by(Environment.sort_order, Environment.key)
    )
    if visible is not None:
        query = query.where(Environment.id.in_(visible))
    environments = await session.scalars(query)
    summary = ProjectSummary.model_validate(project, from_attributes=True)
    return ProjectOut(
        **summary.model_dump(),
        environments=[EnvironmentOut.model_validate(e, from_attributes=True) for e in environments],
    )


async def update_project(session: AsyncSession, project: Project, body: ProjectUpdate) -> None:
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    await session.flush()


async def delete_project(session: AsyncSession, project: Project) -> None:
    """Environments, flags, segments and all targeting go with it (ON DELETE CASCADE)."""
    await session.delete(project)
    await session.flush()

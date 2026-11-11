"""Environment CRUD. A new environment gets a flag_environment row for every flag."""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import key_conflict
from megooflags_api.models import Environment, Project
from megooflags_api.schemas.projects import EnvironmentCreate, EnvironmentUpdate

# One statement whatever the number of flags. The fallthrough is the flag's first variation.
BACKFILL = text(
    """
    INSERT INTO flag_environment (flag_id, environment_id, fallthrough_variation_id)
    SELECT f.id, :env_id,
           (SELECT v.id FROM variation v WHERE v.flag_id = f.id
            ORDER BY v.sort_order, v.key LIMIT 1)
    FROM flag f WHERE f.project_id = :project_id
    """
)


async def create_environment(
    session: AsyncSession, project: Project, body: EnvironmentCreate
) -> Environment:
    # A flag created concurrently would miss this environment, and the backfill would miss the
    # flag: neither sees the other's uncommitted row. create_flag takes FOR SHARE on the project
    # row, so this waits for flag creates in flight and they wait for it.
    await session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    sort_order = body.sort_order
    if sort_order is None:
        last = await session.scalar(
            select(func.max(Environment.sort_order)).where(Environment.project_id == project.id)
        )
        sort_order = 0 if last is None else last + 1
    environment = Environment(
        project_id=project.id,
        key=body.key,
        name=body.name,
        description=body.description,
        sort_order=sort_order,
    )
    session.add(environment)
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("environment", body.key) from None
        raise
    await session.execute(BACKFILL, {"env_id": environment.id, "project_id": project.id})
    return environment


async def list_environments(
    session: AsyncSession, project: Project, visible: set[uuid.UUID] | None = None
) -> list[Environment]:
    """The project's environments; `visible` limits them to those ids (None means all)."""
    query = (
        select(Environment)
        .where(Environment.project_id == project.id)
        .order_by(Environment.sort_order, Environment.key)
    )
    if visible is not None:
        query = query.where(Environment.id.in_(visible))
    return list(await session.scalars(query))


async def update_environment(
    session: AsyncSession, environment: Environment, body: EnvironmentUpdate
) -> None:
    if body.name is not None:
        environment.name = body.name
    if body.description is not None:
        environment.description = body.description
    if body.sort_order is not None:
        environment.sort_order = body.sort_order
    await session.flush()


async def delete_environment(session: AsyncSession, environment: Environment) -> None:
    """The environment's flag state and targeting go with it (ON DELETE CASCADE)."""
    await session.delete(environment)
    await session.flush()

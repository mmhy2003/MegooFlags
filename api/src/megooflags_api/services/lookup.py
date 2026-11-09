"""Key -> row lookups that raise 404 `not_found`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import not_found
from megooflags_api.models import Environment, Flag, Project, Variation


async def get_project(session: AsyncSession, key: str) -> Project:
    project = await session.scalar(select(Project).where(Project.key == key))
    if project is None:
        raise not_found("project", key)
    return project


async def get_environment(session: AsyncSession, project: Project, key: str) -> Environment:
    environment = await session.scalar(
        select(Environment).where(Environment.project_id == project.id, Environment.key == key)
    )
    if environment is None:
        raise not_found("environment", key)
    return environment


async def get_flag(session: AsyncSession, project: Project, key: str) -> Flag:
    """Archived flags are found too: archive only hides them from default lists."""
    flag = await session.scalar(select(Flag).where(Flag.project_id == project.id, Flag.key == key))
    if flag is None:
        raise not_found("flag", key)
    return flag


async def get_variation(session: AsyncSession, flag: Flag, key: str) -> Variation:
    variation = await session.scalar(
        select(Variation).where(Variation.flag_id == flag.id, Variation.key == key)
    )
    if variation is None:
        raise not_found("variation", key)
    return variation

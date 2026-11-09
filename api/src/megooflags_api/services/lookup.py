"""Key -> row lookups that raise 404 `not_found`."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import not_found
from megooflags_api.models import Project


async def get_project(session: AsyncSession, key: str) -> Project:
    project = await session.scalar(select(Project).where(Project.key == key))
    if project is None:
        raise not_found("project", key)
    return project

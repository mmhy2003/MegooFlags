"""Published environment snapshots and their cluster-wide versions."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import EnvironmentSnapshot


async def current_snapshot_version(session: AsyncSession) -> int:
    """The newest snapshot version of any environment, or 0."""
    version: int | None = await session.scalar(select(func.max(EnvironmentSnapshot.version)))
    return version or 0

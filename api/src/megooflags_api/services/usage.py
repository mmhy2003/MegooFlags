"""What still references an entity, for the `in_use` error of archive and delete."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import Environment, Flag, FlagEnvironment, FlagPrerequisite


async def flag_dependents(session: AsyncSession, flag_id: uuid.UUID) -> list[dict[str, str]]:
    """The flag and environment of every config that has `flag_id` as a prerequisite."""
    rows = await session.execute(
        select(Flag.key, Environment.key)
        .select_from(FlagPrerequisite)
        .join(FlagEnvironment, FlagEnvironment.id == FlagPrerequisite.flag_environment_id)
        .join(Flag, Flag.id == FlagEnvironment.flag_id)
        .join(Environment, Environment.id == FlagEnvironment.environment_id)
        .where(FlagPrerequisite.prerequisite_flag_id == flag_id)
        .distinct()
        .order_by(Flag.key, Environment.key)
    )
    return [{"flag": flag, "environment": env} for flag, env in rows]

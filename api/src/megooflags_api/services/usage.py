"""What still references an entity, for the `in_use` error of archive and delete."""

import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import (
    Clause,
    ClauseSegment,
    Environment,
    Flag,
    FlagEnvironment,
    FlagPrerequisite,
    FlagRule,
    FlagTarget,
    RolloutWeight,
    Variation,
)


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


def _environments_of(query: Select[str]) -> Select[str]:
    """Finish a query that has joined FlagEnvironment: its environment keys, deduplicated."""
    return (
        query.join(Environment, Environment.id == FlagEnvironment.environment_id)
        .distinct()
        .order_by(Environment.key)
    )


async def variation_usage(
    session: AsyncSession, variation: Variation, flag: Flag
) -> list[dict[str, str]]:
    """Every place that serves `variation`, one query per kind of use.

    Targets, rules, rollouts and the fallthrough belong to `flag` itself; for prerequisites the
    `flag` field is the dependent flag that requires this variation.
    """
    used_by: list[dict[str, str]] = []
    if flag.off_variation_id == variation.id:
        used_by.append({"usage": "off_variation"})

    in_flag: list[tuple[str, Select[str]]] = [
        (
            "targets",
            select(Environment.key)
            .select_from(FlagTarget)
            .join(FlagEnvironment, FlagEnvironment.id == FlagTarget.flag_environment_id)
            .where(FlagTarget.variation_id == variation.id),
        ),
        (
            "rules",
            select(Environment.key)
            .select_from(FlagRule)
            .join(FlagEnvironment, FlagEnvironment.id == FlagRule.flag_environment_id)
            .where(FlagRule.variation_id == variation.id),
        ),
        (
            # A weight is owned by a rule or, for the fallthrough, by the flag_environment row.
            "rollouts",
            select(Environment.key)
            .select_from(RolloutWeight)
            .outerjoin(FlagRule, FlagRule.id == RolloutWeight.flag_rule_id)
            .join(
                FlagEnvironment,
                FlagEnvironment.id
                == func.coalesce(RolloutWeight.flag_environment_id, FlagRule.flag_environment_id),
            )
            .where(RolloutWeight.variation_id == variation.id),
        ),
        (
            "fallthrough",
            select(Environment.key)
            .select_from(FlagEnvironment)
            .where(FlagEnvironment.fallthrough_variation_id == variation.id),
        ),
    ]
    for usage, query in in_flag:
        for env in await session.scalars(_environments_of(query)):
            used_by.append({"usage": usage, "flag": flag.key, "environment": env})

    rows = await session.execute(
        select(Flag.key, Environment.key)
        .select_from(FlagPrerequisite)
        .join(FlagEnvironment, FlagEnvironment.id == FlagPrerequisite.flag_environment_id)
        .join(Flag, Flag.id == FlagEnvironment.flag_id)
        .join(Environment, Environment.id == FlagEnvironment.environment_id)
        .where(FlagPrerequisite.variation_id == variation.id)
        .distinct()
        .order_by(Flag.key, Environment.key)
    )
    used_by.extend(
        {"usage": "prerequisites", "flag": dependent, "environment": env} for dependent, env in rows
    )
    return used_by


async def segment_dependents(session: AsyncSession, segment_id: uuid.UUID) -> list[dict[str, str]]:
    """The flag and environment of every config whose rules use the segment (in_segment)."""
    rows = await session.execute(
        select(Flag.key, Environment.key)
        .select_from(ClauseSegment)
        .join(Clause, Clause.id == ClauseSegment.clause_id)
        .join(FlagRule, FlagRule.id == Clause.flag_rule_id)
        .join(FlagEnvironment, FlagEnvironment.id == FlagRule.flag_environment_id)
        .join(Flag, Flag.id == FlagEnvironment.flag_id)
        .join(Environment, Environment.id == FlagEnvironment.environment_id)
        .where(ClauseSegment.segment_id == segment_id)
        .distinct()
        .order_by(Flag.key, Environment.key)
    )
    return [{"flag": flag, "environment": env} for flag, env in rows]

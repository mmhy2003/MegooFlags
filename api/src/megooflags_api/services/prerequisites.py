"""Prerequisite flags: references inside the project, and no cycles per environment."""

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import invalid_reference, prerequisite_cycle, validation_error
from megooflags_api.models import Flag, FlagEnvironment, FlagPrerequisite, Variation
from megooflags_api.schemas.targeting import PrerequisiteIn


def has_cycle(edges: dict[uuid.UUID, set[uuid.UUID]], start: uuid.UUID) -> bool:
    """True if `start` is reachable from any of its own prerequisites (iterative DFS)."""
    stack = list(edges.get(start, ()))
    seen: set[uuid.UUID] = set()
    while stack:
        node = stack.pop()
        if node == start:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return False


async def check_prerequisites(
    session: AsyncSession,
    flag: Flag,
    environment_id: uuid.UUID,
    prereqs: list[PrerequisiteIn],
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """The (prerequisite_flag_id, variation_id) of `prereqs` in order, in three queries."""
    seen: set[str] = set()
    for i, prereq in enumerate(prereqs):
        if prereq.flag in seen:
            raise validation_error(
                f"prerequisite flag '{prereq.flag}' is listed twice", f"prerequisites[{i}].flag"
            )
        seen.add(prereq.flag)
        if prereq.flag == flag.key:
            raise prerequisite_cycle(
                "a flag cannot be its own prerequisite", f"prerequisites[{i}].flag"
            )
    if not prereqs:
        return []

    flags = {
        key: (id_, archived_at)
        for key, id_, archived_at in await session.execute(
            select(Flag.key, Flag.id, Flag.archived_at).where(
                Flag.project_id == flag.project_id, Flag.key.in_(seen)
            )
        )
    }
    variations = {
        (flag_id, key): id_
        for flag_id, key, id_ in await session.execute(
            select(Variation.flag_id, Variation.key, Variation.id).where(
                Variation.flag_id.in_([id_ for id_, _ in flags.values()])
            )
        )
    }
    resolved: list[tuple[uuid.UUID, uuid.UUID]] = []
    for i, prereq in enumerate(prereqs):
        if prereq.flag not in flags:
            raise invalid_reference(
                f"unknown prerequisite flag '{prereq.flag}'", f"prerequisites[{i}].flag"
            )
        flag_id, archived_at = flags[prereq.flag]
        if archived_at is not None:
            raise invalid_reference(
                f"prerequisite flag '{prereq.flag}' is archived", f"prerequisites[{i}].flag"
            )
        variation_id = variations.get((flag_id, prereq.variation))
        if variation_id is None:
            raise invalid_reference(
                f"unknown variation '{prereq.variation}' of flag '{prereq.flag}'",
                f"prerequisites[{i}].variation",
            )
        resolved.append((flag_id, variation_id))

    edges: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for dependent, prerequisite in await session.execute(
        select(FlagEnvironment.flag_id, FlagPrerequisite.prerequisite_flag_id)
        .select_from(FlagPrerequisite)
        .join(FlagEnvironment, FlagEnvironment.id == FlagPrerequisite.flag_environment_id)
        .where(FlagEnvironment.environment_id == environment_id)
    ):
        edges[dependent].add(prerequisite)
    edges[flag.id] = {flag_id for flag_id, _ in resolved}  # the proposed edges replace the old
    if has_cycle(edges, flag.id):
        raise prerequisite_cycle("these prerequisites would create a cycle", "prerequisites")
    return resolved

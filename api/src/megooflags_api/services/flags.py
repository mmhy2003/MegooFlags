"""Flag CRUD. A new flag gets a disabled flag_environment row in every environment."""

import uuid

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import (
    in_use,
    invalid_reference,
    key_conflict,
    not_archived,
    validation_error,
)
from megooflags_api.models import Environment, Flag, FlagEnvironment, Project, Variation
from megooflags_api.models.enums import FlagType
from megooflags_api.schemas.common import FlagEnvironmentState
from megooflags_api.schemas.flags import (
    FlagCreate,
    FlagOut,
    FlagSummary,
    FlagUpdate,
    VariationIn,
    VariationOut,
)
from megooflags_api.services.pagination import decode_cursor, page_slice
from megooflags_api.services.usage import flag_dependents
from megooflags_api.services.values import check_value

BOOLEAN_VARIATIONS = [VariationIn(key="true", value=True), VariationIn(key="false", value=False)]

# The fallthrough of every new row is the flag's first variation.
CREATE_FLAG_ENVIRONMENTS = text(
    """
    INSERT INTO flag_environment (flag_id, environment_id, fallthrough_variation_id)
    SELECT :flag_id, e.id, :first_variation_id FROM environment e WHERE e.project_id = :project_id
    """
)


def _check_variations(body: FlagCreate) -> list[VariationIn]:
    if body.type == FlagType.BOOLEAN:
        if body.variations is not None:
            raise validation_error(
                "boolean flags always have the variations true and false", "variations"
            )
        return BOOLEAN_VARIATIONS
    if not body.variations:
        raise validation_error(f"a {body.type} flag needs at least one variation", "variations")
    seen: set[str] = set()
    for i, variation in enumerate(body.variations):
        if variation.key in seen:
            raise validation_error(
                f"variation '{variation.key}' is listed twice", f"variations[{i}].key"
            )
        seen.add(variation.key)
        check_value(body.type, variation.value, f"variations[{i}].value")
    return body.variations


def _off_variation_key(body: FlagCreate, variations: list[VariationIn]) -> str:
    if body.off_variation is None:
        if body.type == FlagType.BOOLEAN:
            return "false"
        raise validation_error("off_variation is required", "off_variation")
    if body.off_variation not in {v.key for v in variations}:
        raise invalid_reference(f"unknown variation '{body.off_variation}'", "off_variation")
    return body.off_variation


async def create_flag(session: AsyncSession, project: Project, body: FlagCreate) -> Flag:
    variations = _check_variations(body)
    off_key = _off_variation_key(body, variations)

    flag = Flag(
        project_id=project.id,
        key=body.key,
        name=body.name,
        description=body.description,
        type=body.type,
        tags=body.tags,
        owner=body.owner,
    )
    session.add(flag)
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("flag", body.key) from None
        raise

    rows = [
        Variation(
            flag_id=flag.id,
            key=v.key,
            name=v.name,
            description=v.description,
            value=v.value,
            sort_order=i,
        )
        for i, v in enumerate(variations)
    ]
    session.add_all(rows)
    await session.flush()
    flag.off_variation_id = next(row.id for row in rows if row.key == off_key)
    await session.execute(
        CREATE_FLAG_ENVIRONMENTS,
        {"flag_id": flag.id, "first_variation_id": rows[0].id, "project_id": project.id},
    )
    await session.flush()
    return flag


def like_pattern(q: str) -> str:
    """A case-insensitive substring pattern, with the LIKE wildcards in `q` matched literally."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def list_flags(
    session: AsyncSession,
    project: Project,
    *,
    q: str | None,
    tags: list[str],
    owner: str | None,
    archived: bool,
    type: FlagType | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[FlagSummary], str | None]:
    """One query for the page of flags, one for their environment states; no variations."""
    query = (
        select(
            Flag.id,
            Flag.key,
            Flag.name,
            Flag.description,
            Flag.type,
            Flag.tags,
            Flag.owner,
            Flag.archived_at,
            Flag.created_at,
            Flag.updated_at,
        )
        .where(
            Flag.project_id == project.id,
            Flag.archived_at.is_not(None) if archived else Flag.archived_at.is_(None),
        )
        .order_by(Flag.key)
        .limit(limit + 1)
    )
    if q:
        pattern = like_pattern(q)
        query = query.where(
            or_(Flag.key.ilike(pattern, escape="\\"), Flag.name.ilike(pattern, escape="\\"))
        )
    if tags:
        query = query.where(Flag.tags.contains(tags))
    if owner is not None:
        query = query.where(Flag.owner == owner)
    if type is not None:
        query = query.where(Flag.type == type)
    if cursor is not None:
        query = query.where(Flag.key > decode_cursor(cursor))

    rows, next_cursor = page_slice(list(await session.execute(query)), limit, lambda r: r.key)
    states = await environment_states(session, [row.id for row in rows]) if rows else {}
    items = [
        FlagSummary(
            key=row.key,
            name=row.name,
            description=row.description,
            type=row.type,
            tags=row.tags,
            owner=row.owner,
            archived=row.archived_at is not None,
            archived_at=row.archived_at,
            environments=states[row.id],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]
    return items, next_cursor


async def update_flag(session: AsyncSession, flag: Flag, body: FlagUpdate) -> None:
    """Only the fields sent change; an explicit `owner: null` clears the owner."""
    if body.name is not None:
        flag.name = body.name
    if body.description is not None:
        flag.description = body.description
    if body.tags is not None:
        flag.tags = body.tags
    if "owner" in body.model_fields_set:
        flag.owner = body.owner
    if body.off_variation is not None:
        variation_id = await session.scalar(
            select(Variation.id).where(
                Variation.flag_id == flag.id, Variation.key == body.off_variation
            )
        )
        if variation_id is None:
            raise invalid_reference(f"unknown variation '{body.off_variation}'", "off_variation")
        flag.off_variation_id = variation_id
    await session.flush()


async def _check_not_prerequisite(session: AsyncSession, flag: Flag) -> None:
    dependents = await flag_dependents(session, flag.id)
    if dependents:
        raise in_use(f"flag '{flag.key}' is a prerequisite of other flags", dependents)


async def archive_flag(session: AsyncSession, flag: Flag) -> None:
    """Archiving an archived flag keeps its original archived_at."""
    await _check_not_prerequisite(session, flag)
    if flag.archived_at is None:
        flag.archived_at = func.now()
        await session.flush()
        await session.refresh(flag)  # load the archived_at the database set


async def restore_flag(session: AsyncSession, flag: Flag) -> None:
    if flag.archived_at is not None:
        flag.archived_at = None
        await session.flush()


async def delete_flag(session: AsyncSession, flag: Flag) -> None:
    """Variations and per-environment config go with the flag (ON DELETE CASCADE)."""
    if flag.archived_at is None:
        raise not_archived(flag.key)
    await _check_not_prerequisite(session, flag)
    await session.delete(flag)
    await session.flush()


async def environment_states(
    session: AsyncSession, flag_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[FlagEnvironmentState]]:
    """The per-environment summary of many flags in one query, in environment order."""
    states: dict[uuid.UUID, list[FlagEnvironmentState]] = {flag_id: [] for flag_id in flag_ids}
    rows = await session.execute(
        select(
            FlagEnvironment.flag_id,
            Environment.key,
            FlagEnvironment.enabled,
            FlagEnvironment.version,
            FlagEnvironment.updated_at,
        )
        .join(Environment, Environment.id == FlagEnvironment.environment_id)
        .where(FlagEnvironment.flag_id.in_(flag_ids))
        .order_by(Environment.sort_order, Environment.key)
    )
    for flag_id, env_key, enabled, version, updated_at in rows:
        states[flag_id].append(
            FlagEnvironmentState(
                environment=env_key, enabled=enabled, version=version, updated_at=updated_at
            )
        )
    return states


async def flag_out(session: AsyncSession, flag: Flag) -> FlagOut:
    variations = list(
        await session.scalars(
            select(Variation)
            .where(Variation.flag_id == flag.id)
            .order_by(Variation.sort_order, Variation.key)
        )
    )
    states = await environment_states(session, [flag.id])
    return FlagOut(
        key=flag.key,
        name=flag.name,
        description=flag.description,
        type=flag.type,
        tags=flag.tags,
        owner=flag.owner,
        archived=flag.archived_at is not None,
        archived_at=flag.archived_at,
        environments=states[flag.id],
        created_at=flag.created_at,
        updated_at=flag.updated_at,
        off_variation=next(v.key for v in variations if v.id == flag.off_variation_id),
        variations=[VariationOut.model_validate(v, from_attributes=True) for v in variations],
    )

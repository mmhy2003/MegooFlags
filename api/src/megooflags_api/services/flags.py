"""Flag CRUD. A new flag gets a disabled flag_environment row in every environment."""

import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import invalid_reference, key_conflict, validation_error
from megooflags_api.models import Environment, Flag, FlagEnvironment, Project, Variation
from megooflags_api.models.enums import FlagType
from megooflags_api.schemas.common import FlagEnvironmentState
from megooflags_api.schemas.flags import FlagCreate, FlagOut, VariationIn, VariationOut
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

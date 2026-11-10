"""Variation CRUD. Boolean flags keep exactly their true and false variations."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import ApiError, in_use, key_conflict, validation_error
from megooflags_api.models import Flag, Variation
from megooflags_api.models.enums import FlagType
from megooflags_api.schemas.flags import VariationIn, VariationUpdate
from megooflags_api.services.usage import variation_usage
from megooflags_api.services.values import check_value


def _fixed(path: str | None = None) -> ApiError:
    return validation_error("boolean flags have fixed variations", path)


def _touch(flag: Flag) -> None:
    flag.updated_at = func.now()  # the ruleset cache fingerprint watches it


async def add_variation(session: AsyncSession, flag: Flag, body: VariationIn) -> Variation:
    """The new variation is listed last."""
    if flag.type == FlagType.BOOLEAN:
        raise _fixed()
    check_value(flag.type, body.value, "value")
    last = await session.scalar(
        select(func.max(Variation.sort_order)).where(Variation.flag_id == flag.id)
    )
    variation = Variation(
        flag_id=flag.id,
        key=body.key,
        name=body.name,
        description=body.description,
        value=body.value,
        sort_order=0 if last is None else last + 1,
    )
    session.add(variation)
    _touch(flag)
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("variation", body.key) from None
        raise
    return variation


async def update_variation(
    session: AsyncSession, flag: Flag, variation: Variation, body: VariationUpdate
) -> None:
    """Only the fields sent change; `value: null` is a value (valid for JSON flags)."""
    if "value" in body.model_fields_set:
        if flag.type == FlagType.BOOLEAN:
            raise _fixed("value")
        check_value(flag.type, body.value, "value")
        variation.value = body.value
    if body.name is not None:
        variation.name = body.name
    if body.description is not None:
        variation.description = body.description
    _touch(flag)
    await session.flush()


async def delete_variation(session: AsyncSession, flag: Flag, variation: Variation) -> None:
    if flag.type == FlagType.BOOLEAN:
        raise _fixed()
    used_by = await variation_usage(session, variation, flag)
    if used_by:
        raise in_use(f"variation '{variation.key}' is in use", used_by)
    await session.delete(variation)
    _touch(flag)
    await session.flush()

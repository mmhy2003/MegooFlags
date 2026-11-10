"""Roles: named permission sets. The built-in ones are read-only; custom ones are CRUD."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.rbac import Permission
from megooflags_api.errors import builtin_role, in_use, key_conflict, not_found
from megooflags_api.models import Role, RoleBinding
from megooflags_api.schemas.identity import RoleIn, RoleOut, RoleUpdate
from megooflags_api.services.pagination import decode_cursor, page_slice


def _catalogue_order(permissions: list[Permission]) -> list[str]:
    """Unique, in catalogue order."""
    chosen = set(permissions)
    return [p.value for p in Permission if p in chosen]


def role_out(role: Role) -> RoleOut:
    held = set(role.permissions)
    return RoleOut(
        id=role.id,
        key=role.key,
        name=role.name,
        description=role.description,
        builtin=role.builtin,
        # In catalogue order; a value dropped from the catalogue grants nothing, so it is not shown.
        permissions=[p for p in Permission if p.value in held],
    )


async def list_roles(
    session: AsyncSession, cursor: str | None, limit: int
) -> tuple[list[RoleOut], str | None]:
    query = select(Role).order_by(Role.key).limit(limit + 1)
    if cursor is not None:
        query = query.where(Role.key > decode_cursor(cursor))
    rows = list(await session.scalars(query))
    page, next_cursor = page_slice(rows, limit, lambda r: r.key)
    return [role_out(r) for r in page], next_cursor


async def get_by_key(session: AsyncSession, key: str) -> Role:
    role = await session.scalar(select(Role).where(Role.key == key))
    if role is None:
        raise not_found("role", key)
    return role


async def create_role(session: AsyncSession, body: RoleIn) -> Role:
    role = Role(
        key=body.key,
        name=body.name,
        description=body.description,
        builtin=False,
        permissions=_catalogue_order(body.permissions),
    )
    session.add(role)
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("role", body.key) from None
        raise
    return role


async def update_role(session: AsyncSession, role: Role, body: RoleUpdate) -> None:
    if role.builtin:
        raise builtin_role(role.key)
    if body.name is not None:
        role.name = body.name
    if body.description is not None:
        role.description = body.description
    if body.permissions is not None:
        role.permissions = _catalogue_order(body.permissions)
    await session.flush()


async def role_in_use(session: AsyncSession, role: Role) -> list[dict[str, str]]:
    """The bindings that use `role` (the first 20)."""
    rows = await session.execute(
        select(RoleBinding.subject_kind, RoleBinding.subject_id)
        .where(RoleBinding.role_id == role.id)
        .order_by(RoleBinding.created_at, RoleBinding.id)
        .limit(20)
    )
    return [
        {"kind": "binding", "subject_kind": kind.value, "subject_id": str(subject_id)}
        for kind, subject_id in rows
    ]


async def delete_role(session: AsyncSession, role: Role) -> None:
    """Refused for built-in roles and while a binding uses the role (the FK is RESTRICT, so a
    binding added concurrently still fails the delete, as 409 in_use)."""
    if role.builtin:
        raise builtin_role(role.key)
    used_by = await role_in_use(session, role)
    if used_by:
        raise in_use(f"role '{role.key}' is bound to subjects", used_by)
    await session.delete(role)
    await session.flush()

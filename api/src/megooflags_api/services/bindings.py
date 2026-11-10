"""Role bindings: a user, team or service token holds a role at org, project or environment
scope. Also the last-admin guard: some active user must keep the admin role at org scope."""

import uuid

from sqlalchemy import ColumnElement, Row, Select, case, select, text, union
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import key_conflict, last_admin, not_found, validation_error
from megooflags_api.models import (
    ApiToken,
    Environment,
    Project,
    Role,
    RoleBinding,
    Team,
    TeamMember,
    User,
)
from megooflags_api.models.enums import ApiTokenKind, ScopeKind, SubjectKind, UserStatus
from megooflags_api.schemas.identity import BindingIn, BindingOut, ScopeFields
from megooflags_api.services.lookup import get_environment, get_project
from megooflags_api.services.pagination import decode_cursor, encode_cursor
from megooflags_api.services.roles import get_by_key

# "MFADMIN1": serialises changes that could remove the last admin, so two of them racing cannot
# each see the other's admin and both succeed.
ADMIN_GUARD_LOCK = 0x4D46_4144_4D49_4E31

ADMIN_ROLE = "admin"


async def resolve_scope(
    session: AsyncSession, scope: ScopeFields
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """The (project_id, environment_id) of a scope; 404 for an unknown project or env."""
    if scope.project is None:
        return None, None
    project = await get_project(session, scope.project)
    if scope.env is None:
        return project.id, None
    return project.id, (await get_environment(session, project, scope.env)).id


async def subject_label(
    session: AsyncSession, subject_kind: SubjectKind, subject_id: uuid.UUID
) -> str | None:
    """The user's email, the team's key or the service token's name; None when there is none."""
    query: Select[str]
    match subject_kind:
        case SubjectKind.USER:
            query = select(User.email).where(User.id == subject_id)
        case SubjectKind.TEAM:
            query = select(Team.key).where(Team.id == subject_id)
        case SubjectKind.SERVICE_TOKEN:
            query = select(ApiToken.name).where(
                ApiToken.id == subject_id, ApiToken.kind == ApiTokenKind.SERVICE
            )
    label: str | None = await session.scalar(query)
    return label


def _label_column() -> ColumnElement[str | None]:
    """`subject_label` as a column of a query over role_binding."""
    return case(
        (
            RoleBinding.subject_kind == SubjectKind.USER,
            select(User.email).where(User.id == RoleBinding.subject_id).scalar_subquery(),
        ),
        (
            RoleBinding.subject_kind == SubjectKind.TEAM,
            select(Team.key).where(Team.id == RoleBinding.subject_id).scalar_subquery(),
        ),
        else_=select(ApiToken.name)
        .where(ApiToken.id == RoleBinding.subject_id, ApiToken.kind == ApiTokenKind.SERVICE)
        .scalar_subquery(),
    )


def _out_query() -> Select[RoleBinding, str, str | None, str | None, str | None]:
    return (
        select(RoleBinding, Role.key, Project.key, Environment.key, _label_column())
        .join(Role, Role.id == RoleBinding.role_id)
        .outerjoin(Project, Project.id == RoleBinding.project_id)
        .outerjoin(Environment, Environment.id == RoleBinding.environment_id)
    )


def _out(row: Row[RoleBinding, str, str | None, str | None, str | None]) -> BindingOut:
    binding, role, project, env, label = row
    return BindingOut(
        id=binding.id,
        subject_kind=binding.subject_kind,
        subject_id=binding.subject_id,
        subject_label=label,
        role=role,
        scope=binding.scope,
        project=project,
        env=env,
        source=binding.source,
        created_at=binding.created_at,
    )


async def binding_out(session: AsyncSession, binding_id: uuid.UUID) -> BindingOut:
    row = (await session.execute(_out_query().where(RoleBinding.id == binding_id))).one()
    return _out(row)


async def get_binding(session: AsyncSession, binding_id: uuid.UUID) -> RoleBinding:
    binding = await session.get(RoleBinding, binding_id)
    if binding is None:
        raise not_found("binding", str(binding_id))
    return binding


async def list_bindings(
    session: AsyncSession,
    visible: ColumnElement[bool],
    *,
    subject_kind: SubjectKind | None,
    subject_id: uuid.UUID | None,
    scope: ScopeKind | None,
    project_id: uuid.UUID | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[BindingOut], str | None]:
    """The bindings matching `visible` and the filters, by id. `project_id` matches project and
    environment bindings in that project."""
    query = _out_query().where(visible).order_by(RoleBinding.id).limit(limit + 1)
    if subject_kind is not None:
        query = query.where(RoleBinding.subject_kind == subject_kind)
    if subject_id is not None:
        query = query.where(RoleBinding.subject_id == subject_id)
    if scope is not None:
        query = query.where(RoleBinding.scope == scope)
    if project_id is not None:
        query = query.where(RoleBinding.project_id == project_id)
    if cursor is not None:
        try:
            after = uuid.UUID(decode_cursor(cursor))
        except ValueError:
            raise validation_error("invalid cursor", "cursor") from None
        query = query.where(RoleBinding.id > after)
    rows = (await session.execute(query)).all()
    items = [_out(row) for row in rows[:limit]]
    next_cursor = encode_cursor(str(items[-1].id)) if len(rows) > limit else None
    return items, next_cursor


async def create_binding(
    session: AsyncSession, body: BindingIn, *, source: str = "manual"
) -> RoleBinding:
    """404 for an unknown role, project, environment or subject; 409 for a duplicate."""
    role = await get_by_key(session, body.role)
    project_id, environment_id = await resolve_scope(session, body)
    if await subject_label(session, body.subject_kind, body.subject_id) is None:
        raise not_found(body.subject_kind.value.replace("_", " "), str(body.subject_id))
    binding = RoleBinding(
        subject_kind=body.subject_kind,
        subject_id=body.subject_id,
        role_id=role.id,
        scope=body.scope,
        project_id=project_id,
        environment_id=environment_id,
        source=source,
    )
    session.add(binding)
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            where = "/".join(k for k in (body.scope.value, body.project, body.env) if k)
            raise key_conflict("binding", f"{body.role} at {where}") from None
        raise
    return binding


async def delete_binding(session: AsyncSession, binding: RoleBinding, role_key: str) -> None:
    """409 last_admin when it would leave no active org admin."""
    reduces_admins = (
        role_key == ADMIN_ROLE
        and binding.scope == ScopeKind.ORG
        and binding.subject_kind != SubjectKind.SERVICE_TOKEN
    )
    await session.delete(binding)
    await session.flush()
    if reduces_admins:
        await guard_last_admin(session)


async def active_org_admin_ids(session: AsyncSession) -> set[uuid.UUID]:
    """The active users holding the built-in admin role at org scope, directly or via a team."""
    admin_role = select(Role.id).where(Role.key == ADMIN_ROLE, Role.builtin).scalar_subquery()
    org_admin = (RoleBinding.scope == ScopeKind.ORG) & (RoleBinding.role_id == admin_role)
    direct = select(RoleBinding.subject_id).where(
        org_admin, RoleBinding.subject_kind == SubjectKind.USER
    )
    via_team = (
        select(TeamMember.user_id)
        .join(
            RoleBinding,
            (RoleBinding.subject_kind == SubjectKind.TEAM)
            & (RoleBinding.subject_id == TeamMember.team_id),
        )
        .where(org_admin)
    )
    ids = await session.scalars(
        select(User.id).where(
            User.status == UserStatus.ACTIVE, User.id.in_(union(direct, via_team))
        )
    )
    return set(ids)


async def guard_last_admin(session: AsyncSession) -> None:
    """Call after flushing a change that could remove admins: raises 409 last_admin when no
    active org admin is left. The lock makes a racing change wait and then see this one."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMIN_GUARD_LOCK})
    if not await active_org_admin_ids(session):
        raise last_admin()

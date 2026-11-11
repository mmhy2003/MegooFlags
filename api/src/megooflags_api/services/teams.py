"""Teams and their members. Removing members or a team is subject to the last-admin guard."""

import uuid

from sqlalchemy import ScalarSelect, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import key_conflict, not_found
from megooflags_api.models import RoleBinding, Team, TeamMember, User
from megooflags_api.models.enums import SubjectKind
from megooflags_api.schemas.identity import TeamMemberOut, TeamOut
from megooflags_api.services.bindings import active_org_admin_ids, guard_last_admin
from megooflags_api.services.pagination import decode_cursor, page_slice
from megooflags_api.services.users import get_user, user_out


def _member_count() -> ScalarSelect[int]:
    return (
        select(func.count())
        .select_from(TeamMember)
        .where(TeamMember.team_id == Team.id)
        .scalar_subquery()
    )


def team_out(team: Team, member_count: int) -> TeamOut:
    return TeamOut(
        id=team.id,
        key=team.key,
        name=team.name,
        created_at=team.created_at,
        member_count=member_count,
    )


async def list_teams(
    session: AsyncSession, cursor: str | None, limit: int
) -> tuple[list[TeamOut], str | None]:
    query = select(Team, _member_count()).order_by(Team.key).limit(limit + 1)
    if cursor is not None:
        query = query.where(Team.key > decode_cursor(cursor))
    rows = list((await session.execute(query)).all())
    page, next_cursor = page_slice(rows, limit, lambda row: row[0].key)
    return [team_out(team, count) for team, count in page], next_cursor


async def get_by_key(session: AsyncSession, key: str) -> Team:
    team = await session.scalar(select(Team).where(Team.key == key))
    if team is None:
        raise not_found("team", key)
    return team


async def read_team(session: AsyncSession, team: Team) -> TeamOut:
    count = await session.scalar(
        select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team.id)
    )
    return team_out(team, count or 0)


async def create_team(session: AsyncSession, key: str, name: str) -> Team:
    team = Team(key=key, name=name)
    session.add(team)
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("team", key) from None
        raise
    return team


async def delete_team(session: AsyncSession, team: Team) -> None:
    """Delete the team's role bindings, then the team (its memberships cascade); 409 last_admin
    when a member was an org admin and no active org admin is left."""
    members = set(
        await session.scalars(select(TeamMember.user_id).where(TeamMember.team_id == team.id))
    )
    had_admins = bool(members & await active_org_admin_ids(session))
    await session.execute(
        delete(RoleBinding).where(
            RoleBinding.subject_kind == SubjectKind.TEAM, RoleBinding.subject_id == team.id
        )
    )
    await session.delete(team)
    await session.flush()
    if had_admins:
        await guard_last_admin(session)


async def list_members(
    session: AsyncSession, team: Team, cursor: str | None, limit: int
) -> tuple[list[TeamMemberOut], str | None]:
    """The team's members by lower(email)."""
    sort_key = func.lower(User.email)
    query = (
        select(User, TeamMember.source)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team.id)
        .order_by(sort_key)
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(sort_key > decode_cursor(cursor))
    rows = list((await session.execute(query)).all())
    page, next_cursor = page_slice(rows, limit, lambda row: row[0].email.lower())
    return [TeamMemberOut(user=user_out(u), source=source) for u, source in page], next_cursor


async def get_member(session: AsyncSession, team: Team, user_id: uuid.UUID) -> TeamMemberOut:
    """404 when the user is not a member of the team."""
    row = (
        await session.execute(
            select(User, TeamMember.source)
            .join(TeamMember, TeamMember.user_id == User.id)
            .where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
        )
    ).one_or_none()
    if row is None:
        raise not_found("team member", str(user_id))
    user, source = row
    return TeamMemberOut(user=user_out(user), source=source)


async def add_member(
    session: AsyncSession, team: Team, user_id: uuid.UUID, *, source: str = "manual"
) -> TeamMemberOut:
    """404 for an unknown user; 409 when the user is already a member."""
    user = await get_user(session, user_id)
    out = TeamMemberOut(user=user_out(user), source=source)
    # A failed flush expires the loaded rows, so the message is built before it.
    label = f"{user.email} in {team.key}"
    session.add(TeamMember(team_id=team.id, user_id=user.id, source=source))
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("team member", label) from None
        raise
    return out


async def remove_member(session: AsyncSession, team: Team, user_id: uuid.UUID) -> None:
    """409 last_admin when the user held admin only through teams and was the last org admin."""
    was_admin = user_id in await active_org_admin_ids(session)
    await session.execute(
        delete(TeamMember).where(TeamMember.team_id == team.id, TeamMember.user_id == user_id)
    )
    if was_admin:
        await guard_last_admin(session)

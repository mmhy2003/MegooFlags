"""Teams (`/teams`) and their members (`/teams/{key}/members`)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, authenticated, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models.enums import SubjectKind
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.identity import (
    TeamIn,
    TeamMemberIn,
    TeamMemberOut,
    TeamOut,
    TeamUpdate,
)
from megooflags_api.services import bindings, teams
from megooflags_api.services.audit import AuditDep, snapshot

router = APIRouter(prefix="/teams", tags=["teams"], responses=ERROR_RESPONSES)

Authenticated = Annotated[Authz, Depends(authenticated())]
ManageMembers = Annotated[Authz, Depends(require(Permission.MEMBER_MANAGE))]


@router.get("")
async def list_teams(
    session: SessionDep,
    authz: Authenticated,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[TeamOut]:
    items, next_cursor = await teams.list_teams(session, cursor, limit)
    return Page[TeamOut](items=items, next_cursor=next_cursor)


@router.post("", status_code=201)
async def create_team(
    session: SessionDep, authz: ManageMembers, audit: AuditDep, body: TeamIn
) -> TeamOut:
    out = teams.team_out(await teams.create_team(session, body.key, body.name), 0)
    await audit.record("team.create", "team", out.key, after=snapshot(out))
    await session.commit()
    return out


@router.get("/{key}")
async def read_team(session: SessionDep, authz: Authenticated, key: str) -> TeamOut:
    return await teams.read_team(session, await teams.get_by_key(session, key))


@router.patch("/{key}")
async def update_team(
    session: SessionDep, authz: ManageMembers, audit: AuditDep, key: str, body: TeamUpdate
) -> TeamOut:
    team = await teams.get_by_key(session, key)
    before = await teams.read_team(session, team)
    if body.name is not None:
        team.name = body.name
        await session.flush()
    out = teams.team_out(team, before.member_count)
    await audit.record("team.update", "team", key, before=snapshot(before), after=snapshot(out))
    await session.commit()
    return out


@router.delete("/{key}", status_code=204)
async def delete_team(
    session: SessionDep, authz: ManageMembers, audit: AuditDep, key: str
) -> Response:
    """Delete the team and its role bindings; 409 last_admin if that leaves no org admin."""
    team = await teams.get_by_key(session, key)
    before = await teams.read_team(session, team)
    await teams.delete_team(session, team)
    await audit.record("team.delete", "team", key, before=snapshot(before))
    await session.commit()
    return Response(status_code=204)


@router.get("/{key}/members")
async def list_members(
    session: SessionDep,
    authz: Authenticated,
    key: str,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[TeamMemberOut]:
    team = await teams.get_by_key(session, key)
    items, next_cursor = await teams.list_members(session, team, cursor, limit)
    return Page[TeamMemberOut](items=items, next_cursor=next_cursor)


@router.post("/{key}/members", status_code=201)
async def add_member(
    session: SessionDep, authz: ManageMembers, audit: AuditDep, key: str, body: TeamMemberIn
) -> TeamMemberOut:
    """The caller must hold every permission the team's bindings grant."""
    team = await teams.get_by_key(session, key)
    await bindings.check_bindings_grantable(session, authz.authorizer, SubjectKind.TEAM, team.id)
    out = await teams.add_member(session, team, body.user_id)
    await audit.record("team.member.add", "team", key, after=snapshot(out))
    await session.commit()
    return out


@router.delete("/{key}/members/{user_id}", status_code=204)
async def remove_member(
    session: SessionDep, authz: ManageMembers, audit: AuditDep, key: str, user_id: uuid.UUID
) -> Response:
    """409 last_admin if the user was the last org admin and held that only through teams."""
    team = await teams.get_by_key(session, key)
    before = await teams.get_member(session, team, user_id)
    await teams.remove_member(session, team, user_id)
    await audit.record("team.member.remove", "team", key, before=snapshot(before))
    await session.commit()
    return Response(status_code=204)

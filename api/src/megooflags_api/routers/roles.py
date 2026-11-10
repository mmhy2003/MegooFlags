"""Roles (`/roles`) and role bindings (`/bindings`)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import or_

from megooflags_api.auth.rbac import Permission, own_bindings
from megooflags_api.auth.require import Authz, authenticated, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models import RoleBinding
from megooflags_api.models.enums import ScopeKind, SubjectKind
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.identity import BindingIn, BindingOut, RoleIn, RoleOut, RoleUpdate
from megooflags_api.services import bindings, roles
from megooflags_api.services.audit import AuditDep, snapshot
from megooflags_api.services.lookup import get_project

router = APIRouter(responses=ERROR_RESPONSES)

Authenticated = Annotated[Authz, Depends(authenticated())]
ManageRoles = Annotated[Authz, Depends(require(Permission.ROLE_MANAGE))]

MANAGED_BY: dict[SubjectKind, Permission] = {
    SubjectKind.USER: Permission.MEMBER_MANAGE,
    SubjectKind.TEAM: Permission.MEMBER_MANAGE,
    SubjectKind.SERVICE_TOKEN: Permission.TOKEN_MANAGE,
}


@router.get("/roles", tags=["roles"])
async def list_roles(
    session: SessionDep,
    authz: Authenticated,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[RoleOut]:
    items, next_cursor = await roles.list_roles(session, cursor, limit)
    return Page[RoleOut](items=items, next_cursor=next_cursor)


@router.get("/roles/{key}", tags=["roles"])
async def read_role(session: SessionDep, authz: Authenticated, key: str) -> RoleOut:
    return roles.role_out(await roles.get_by_key(session, key))


@router.post("/roles", status_code=201, tags=["roles"])
async def create_role(
    session: SessionDep, authz: ManageRoles, audit: AuditDep, body: RoleIn
) -> RoleOut:
    out = roles.role_out(await roles.create_role(session, body))
    await audit.record("role.create", "role", out.key, after=snapshot(out))
    await session.commit()
    return out


@router.patch("/roles/{key}", tags=["roles"])
async def update_role(
    session: SessionDep, authz: ManageRoles, audit: AuditDep, key: str, body: RoleUpdate
) -> RoleOut:
    role = await roles.get_by_key(session, key)
    before = roles.role_out(role)
    await roles.update_role(session, role, body)
    out = roles.role_out(role)
    await audit.record("role.update", "role", key, before=snapshot(before), after=snapshot(out))
    await session.commit()
    return out


@router.delete("/roles/{key}", status_code=204, tags=["roles"])
async def delete_role(
    session: SessionDep, authz: ManageRoles, audit: AuditDep, key: str
) -> Response:
    role = await roles.get_by_key(session, key)
    before = roles.role_out(role)
    await roles.delete_role(session, role)
    await audit.record("role.delete", "role", key, before=snapshot(before))
    await session.commit()
    return Response(status_code=204)


@router.get("/bindings", tags=["bindings"])
async def list_bindings(
    session: SessionDep,
    authz: Authenticated,
    subject_kind: SubjectKind | None = None,
    subject_id: uuid.UUID | None = None,
    scope: ScopeKind | None = None,
    project: str | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[BindingOut]:
    """Everyone sees their own bindings (and their teams'); `member.manage` also shows user and
    team bindings, `token.manage` service token bindings."""
    managed = [k for k, perm in MANAGED_BY.items() if authz.authorizer.can(perm)]
    visible = or_(RoleBinding.subject_kind.in_(managed), own_bindings(authz.principal))
    project_id = None if project is None else (await get_project(session, project)).id
    items, next_cursor = await bindings.list_bindings(
        session,
        visible,
        subject_kind=subject_kind,
        subject_id=subject_id,
        scope=scope,
        project_id=project_id,
        cursor=cursor,
        limit=limit,
    )
    return Page[BindingOut](items=items, next_cursor=next_cursor)


@router.post("/bindings", status_code=201, tags=["bindings"])
async def create_binding(
    session: SessionDep, authz: Authenticated, audit: AuditDep, body: BindingIn
) -> BindingOut:
    authz.check(MANAGED_BY[body.subject_kind])
    binding = await bindings.create_binding(session, body)
    out = await bindings.binding_out(session, binding.id)
    await audit.record(
        "binding.create",
        "binding",
        str(out.id),
        project_id=binding.project_id,
        environment_id=binding.environment_id,
        after=snapshot(out),
    )
    await session.commit()
    return out


@router.delete("/bindings/{id}", status_code=204, tags=["bindings"])
async def delete_binding(
    session: SessionDep, authz: Authenticated, audit: AuditDep, id: uuid.UUID
) -> Response:
    binding = await bindings.get_binding(session, id)
    authz.check(MANAGED_BY[binding.subject_kind])
    before = await bindings.binding_out(session, id)
    project_id, environment_id = binding.project_id, binding.environment_id
    await bindings.delete_binding(session, binding, before.role)
    await audit.record(
        "binding.delete",
        "binding",
        str(id),
        project_id=project_id,
        environment_id=environment_id,
        before=snapshot(before),
    )
    await session.commit()
    return Response(status_code=204)

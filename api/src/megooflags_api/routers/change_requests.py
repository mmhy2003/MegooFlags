import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES, not_found
from megooflags_api.models import Environment, Project
from megooflags_api.models.enums import ChangeRequestStatus
from megooflags_api.schemas.change_requests import (
    ChangeRequestCreate,
    ChangeRequestOut,
    ChangeRequestSummary,
    CommentIn,
    CommentOut,
)
from megooflags_api.schemas.common import Page
from megooflags_api.services import change_requests
from megooflags_api.services.audit import Actor, AuditDep
from megooflags_api.services.lookup import get_environment, get_flag

router = APIRouter(tags=["change-requests"], responses=ERROR_RESPONSES)

ReadProject = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]

CHANGE_REQUESTS = "/projects/{project}/change-requests"


@router.get(CHANGE_REQUESTS)
async def list_change_requests(
    session: SessionDep,
    authz: ReadProject,
    project: str,
    status: ChangeRequestStatus | None = None,
    env: Annotated[str | None, Query(description="An environment key.")] = None,
    flag: Annotated[str | None, Query(description="A flag key.")] = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[ChangeRequestSummary]:
    """Change requests in the environments the caller can see, newest first."""
    project_row = _project(authz)
    items, next_cursor = await change_requests.list_change_requests(
        session,
        project_row,
        visible=_visible(authz),
        status=status,
        env=env,
        flag=flag,
        cursor=cursor,
        limit=limit,
    )
    return Page[ChangeRequestSummary](items=items, next_cursor=next_cursor)


@router.post(CHANGE_REQUESTS, status_code=201)
async def create_change_request(
    session: SessionDep,
    authz: ReadProject,
    audit: AuditDep,
    project: str,
    body: ChangeRequestCreate,
) -> ChangeRequestOut:
    """Open a change request; it needs the permission of the change it carries.

    The change is dry-run first, so an invalid payload or a stale config `version` is refused
    now. Change requests are allowed in unprotected environments too.
    """
    project_row = _project(authz)
    env_row = await _visible_environment(session, authz, body.environment)
    flag_row = await get_flag(session, project_row, body.flag)
    payload = change_requests.parse_payload(body.kind, body.payload)
    authz.check(change_requests.required_permission(body.kind, payload), project_row.id, env_row.id)
    cr = await change_requests.create(
        session,
        audit,
        Actor.of(authz.principal),
        project_row,
        env_row,
        flag_row,
        body,
        datetime.now(UTC),
    )
    out = await change_requests.out(session, cr)
    await session.commit()
    return out


@router.get(f"{CHANGE_REQUESTS}/{{id}}")
async def read_change_request(
    session: SessionDep, authz: ReadProject, project: str, id: uuid.UUID
) -> ChangeRequestOut:
    cr = await change_requests.get(session, _project(authz), id, _visible(authz))
    return await change_requests.out(session, cr)


@router.post(f"{CHANGE_REQUESTS}/{{id}}/comments", status_code=201)
async def comment_on_change_request(
    session: SessionDep,
    authz: ReadProject,
    audit: AuditDep,
    project: str,
    id: uuid.UUID,
    body: CommentIn,
) -> CommentOut:
    """Comment on an open change request; 409 once it is closed."""
    cr = await change_requests.get(session, _project(authz), id, _visible(authz), lock=True)
    out = await change_requests.comment(session, audit, Actor.of(authz.principal), cr, body.body)
    await session.commit()
    return out


@router.post(f"{CHANGE_REQUESTS}/{{id}}/cancel")
async def cancel_change_request(
    session: SessionDep, authz: ReadProject, audit: AuditDep, project: str, id: uuid.UUID
) -> ChangeRequestOut:
    """Close an open change request without applying it; for its author or project.manage."""
    project_row = _project(authz)
    cr = await change_requests.get(session, project_row, id, _visible(authz), lock=True)
    if authz.principal.actor_id != cr.author_id:
        authz.check(Permission.PROJECT_MANAGE, project_row.id)
    await change_requests.cancel(session, audit, cr, datetime.now(UTC))
    out = await change_requests.out(session, cr)
    await session.commit()
    return out


def _project(authz: Authz) -> Project:
    assert authz.project is not None  # every route here has {project}
    return authz.project


def _visible(authz: Authz) -> set[uuid.UUID] | None:
    """The project's environments the caller can see; None means all."""
    return authz.authorizer.visible_environment_ids(_project(authz).id)


async def _visible_environment(session: AsyncSession, authz: Authz, key: str) -> Environment:
    """The project's environment `key`; 404 when it does not exist or is hidden from the caller."""
    project_row = _project(authz)
    env_row = await get_environment(session, project_row, key)
    if not authz.authorizer.can_see_environment(project_row.id, env_row.id):
        raise not_found("environment", key)
    return env_row

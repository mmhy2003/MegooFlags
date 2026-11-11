import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models import Flag, Project
from megooflags_api.models.enums import FlagType
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.flags import (
    FlagCreate,
    FlagOut,
    FlagSummary,
    FlagUpdate,
    LifecycleIn,
    LifecycleState,
    VariationIn,
    VariationOut,
    VariationUpdate,
)
from megooflags_api.services import flags, lifecycle, variations
from megooflags_api.services.audit import AuditDep, Auditor, snapshot
from megooflags_api.services.changes import ChangesDep
from megooflags_api.services.lookup import get_flag, get_variation

router = APIRouter(tags=["flags"], responses=ERROR_RESPONSES)

ReadProject = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]
CreateFlag = Annotated[Authz, Depends(require(Permission.FLAG_CREATE))]
UpdateFlag = Annotated[Authz, Depends(require(Permission.FLAG_UPDATE))]
ArchiveFlag = Annotated[Authz, Depends(require(Permission.FLAG_ARCHIVE))]


def _project(authz: Authz) -> Project:
    assert authz.project is not None  # every route here has {project}
    return authz.project


def _visible(authz: Authz) -> set[uuid.UUID] | None:
    """The project's environments the caller can see; None means all."""
    return authz.authorizer.visible_environment_ids(_project(authz).id)


@router.get("/projects/{project}/flags")
async def list_flags(
    session: SessionDep,
    authz: ReadProject,
    project: str,
    q: str | None = Query(None, description="Case-insensitive substring of the key or name."),
    tag: Annotated[
        list[str] | None, Query(description="Repeatable: flags must have every tag given.")
    ] = None,
    owner: str | None = None,
    archived: bool = Query(False, description="List only archived flags instead of live ones."),
    type: FlagType | None = None,
    lifecycle: Annotated[
        LifecycleState | None,
        Query(
            description='Only flags in this state; wins over `archived` ("archived" lists '
            "archived flags, the other states live ones)."
        ),
    ] = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[FlagSummary]:
    items, next_cursor = await flags.list_flags(
        session,
        _project(authz),
        q=q,
        tags=tag or [],
        owner=owner,
        archived=archived,
        type=type,
        cursor=cursor,
        limit=limit,
        visible=_visible(authz),
        lifecycle=lifecycle,
    )
    return Page[FlagSummary](items=items, next_cursor=next_cursor)


@router.post("/projects/{project}/flags", status_code=201)
async def create_flag(
    session: SessionDep,
    authz: CreateFlag,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    body: FlagCreate,
) -> FlagOut:
    row = await flags.create_flag(session, _project(authz), body)
    out = await flags.flag_out(session, row, _visible(authz))
    await audit.record(
        "flag.create", "flag", row.key, project_id=row.project_id, after=snapshot(out)
    )
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.get("/projects/{project}/flags/{flag}")
async def read_flag(session: SessionDep, authz: ReadProject, project: str, flag: str) -> FlagOut:
    row = await get_flag(session, _project(authz), flag)
    return await flags.flag_out(session, row, _visible(authz))


@router.patch("/projects/{project}/flags/{flag}")
async def update_flag(
    session: SessionDep,
    authz: UpdateFlag,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    body: FlagUpdate,
) -> FlagOut:
    row = await get_flag(session, _project(authz), flag)
    before = await flags.flag_out(session, row, _visible(authz))
    await flags.update_flag(session, row, body)
    out = await flags.flag_out(session, row, _visible(authz))
    await _record_flag(audit, "flag.update", row, before, out)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.put("/projects/{project}/flags/{flag}/lifecycle")
async def set_flag_lifecycle(
    session: SessionDep,
    authz: UpdateFlag,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    body: LifecycleIn,
) -> FlagOut:
    """Mark the flag permanent (never stale), or clear the mark."""
    row = await get_flag(session, _project(authz), flag)
    before = await flags.flag_out(session, row, _visible(authz))
    await lifecycle.set_permanent(session, row, body.permanent)
    out = await flags.flag_out(session, row, _visible(authz))
    await _record_flag(audit, "flag.lifecycle.update", row, before, out)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.post("/projects/{project}/flags/{flag}/archive")
async def archive_flag(
    session: SessionDep,
    authz: ArchiveFlag,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
) -> FlagOut:
    row = await get_flag(session, _project(authz), flag)
    before = await flags.flag_out(session, row, _visible(authz))
    await flags.archive_flag(session, row)
    out = await flags.flag_out(session, row, _visible(authz))
    await _record_flag(audit, "flag.archive", row, before, out)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.post("/projects/{project}/flags/{flag}/restore")
async def restore_flag(
    session: SessionDep,
    authz: ArchiveFlag,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
) -> FlagOut:
    row = await get_flag(session, _project(authz), flag)
    before = await flags.flag_out(session, row, _visible(authz))
    await flags.restore_flag(session, row)
    out = await flags.flag_out(session, row, _visible(authz))
    await _record_flag(audit, "flag.restore", row, before, out)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.delete("/projects/{project}/flags/{flag}", status_code=204)
async def delete_flag(
    session: SessionDep,
    authz: ArchiveFlag,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
) -> Response:
    row = await get_flag(session, _project(authz), flag)
    project_id = row.project_id
    before = await flags.flag_out(session, row, _visible(authz))
    await _record_flag(audit, "flag.delete", row, before, None)
    await flags.delete_flag(session, row)
    await session.commit()
    await bus.publish(project_id)
    return Response(status_code=204)


@router.post("/projects/{project}/flags/{flag}/variations", status_code=201)
async def add_variation(
    session: SessionDep,
    authz: UpdateFlag,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    body: VariationIn,
) -> VariationOut:
    flag_row = await get_flag(session, _project(authz), flag)
    row = await variations.add_variation(session, flag_row, body)
    out = VariationOut.model_validate(row, from_attributes=True)
    await _record_variation(audit, "flag.variation.create", flag_row, row.key, None, out)
    await session.commit()
    await bus.publish(flag_row.project_id)
    return out


@router.patch("/projects/{project}/flags/{flag}/variations/{variation}")
async def update_variation(
    session: SessionDep,
    authz: UpdateFlag,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    variation: str,
    body: VariationUpdate,
) -> VariationOut:
    flag_row = await get_flag(session, _project(authz), flag)
    row = await get_variation(session, flag_row, variation)
    before = VariationOut.model_validate(row, from_attributes=True)
    await variations.update_variation(session, flag_row, row, body)
    out = VariationOut.model_validate(row, from_attributes=True)
    await _record_variation(audit, "flag.variation.update", flag_row, row.key, before, out)
    await session.commit()
    await bus.publish(flag_row.project_id)
    return out


@router.delete("/projects/{project}/flags/{flag}/variations/{variation}", status_code=204)
async def delete_variation(
    session: SessionDep,
    authz: UpdateFlag,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    flag: str,
    variation: str,
) -> Response:
    flag_row = await get_flag(session, _project(authz), flag)
    row = await get_variation(session, flag_row, variation)
    before = VariationOut.model_validate(row, from_attributes=True)
    await _record_variation(audit, "flag.variation.delete", flag_row, row.key, before, None)
    await variations.delete_variation(session, flag_row, row)
    await session.commit()
    await bus.publish(flag_row.project_id)
    return Response(status_code=204)


async def _record_flag(
    audit: Auditor, action: str, row: Flag, before: FlagOut | None, after: FlagOut | None
) -> None:
    await audit.record(
        action,
        "flag",
        row.key,
        project_id=row.project_id,
        before=None if before is None else snapshot(before),
        after=None if after is None else snapshot(after),
    )


async def _record_variation(
    audit: Auditor,
    action: str,
    flag: Flag,
    variation: str,
    before: VariationOut | None,
    after: VariationOut | None,
) -> None:
    await audit.record(
        action,
        "variation",
        f"{flag.key}/{variation}",
        project_id=flag.project_id,
        before=None if before is None else snapshot(before),
        after=None if after is None else snapshot(after),
    )

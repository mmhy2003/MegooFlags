from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models import Project
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.segments import (
    SegmentConfigIn,
    SegmentCreate,
    SegmentOut,
    SegmentSummary,
    SegmentUpdate,
)
from megooflags_api.services import segments
from megooflags_api.services.audit import AuditDep, snapshot
from megooflags_api.services.changes import ChangesDep
from megooflags_api.services.lookup import get_segment

router = APIRouter(tags=["segments"], responses=ERROR_RESPONSES)

ReadProject = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]
WriteSegments = Annotated[Authz, Depends(require(Permission.SEGMENT_WRITE))]


def _project(authz: Authz) -> Project:
    assert authz.project is not None  # every route here has {project}
    return authz.project


@router.get("/projects/{project}/segments")
async def list_segments(
    session: SessionDep,
    authz: ReadProject,
    project: str,
    q: str | None = Query(None, description="Case-insensitive substring of the key or name."),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[SegmentSummary]:
    items, next_cursor = await segments.list_segments(
        session, _project(authz), q=q, cursor=cursor, limit=limit
    )
    return Page[SegmentSummary](items=items, next_cursor=next_cursor)


@router.post("/projects/{project}/segments", status_code=201)
async def create_segment(
    session: SessionDep,
    authz: WriteSegments,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    body: SegmentCreate,
) -> SegmentOut:
    row = await segments.create_segment(session, _project(authz), body)
    out = await segments.segment_out(session, row)
    await audit.record(
        "segment.create", "segment", row.key, project_id=row.project_id, after=snapshot(out)
    )
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.get("/projects/{project}/segments/{segment}")
async def read_segment(
    session: SessionDep, authz: ReadProject, project: str, segment: str
) -> SegmentOut:
    row = await get_segment(session, _project(authz), segment)
    return await segments.segment_out(session, row)


@router.put("/projects/{project}/segments/{segment}")
async def replace_segment_config(
    session: SessionDep,
    authz: WriteSegments,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    segment: str,
    body: SegmentConfigIn,
) -> SegmentOut:
    row = await get_segment(session, _project(authz), segment)
    before = await segments.segment_out(session, row)
    await segments.replace_segment_config(session, row, body)
    out = await segments.segment_out(session, row)
    await audit.record(
        "segment.config.update",
        "segment",
        row.key,
        project_id=row.project_id,
        before=snapshot(before),
        after=snapshot(out),
    )
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.patch("/projects/{project}/segments/{segment}")
async def update_segment(
    session: SessionDep,
    authz: WriteSegments,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    segment: str,
    body: SegmentUpdate,
) -> SegmentOut:
    row = await get_segment(session, _project(authz), segment)
    before = await segments.segment_out(session, row)
    await segments.update_segment(session, row, body)
    out = await segments.segment_out(session, row)
    await audit.record(
        "segment.update",
        "segment",
        row.key,
        project_id=row.project_id,
        before=snapshot(before),
        after=snapshot(out),
    )
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.delete("/projects/{project}/segments/{segment}", status_code=204)
async def delete_segment(
    session: SessionDep,
    authz: WriteSegments,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    segment: str,
) -> Response:
    row = await get_segment(session, _project(authz), segment)
    project_id = row.project_id
    before = await segments.segment_out(session, row)
    await audit.record(
        "segment.delete", "segment", row.key, project_id=project_id, before=snapshot(before)
    )
    await segments.delete_segment(session, row)
    await session.commit()
    await bus.publish(project_id)
    return Response(status_code=204)

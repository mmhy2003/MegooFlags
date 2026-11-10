from fastapi import APIRouter, Query, Response

from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.segments import (
    SegmentConfigIn,
    SegmentCreate,
    SegmentOut,
    SegmentSummary,
    SegmentUpdate,
)
from megooflags_api.services import segments
from megooflags_api.services.changes import ChangesDep
from megooflags_api.services.lookup import get_project, get_segment

router = APIRouter(tags=["segments"], responses=ERROR_RESPONSES)


@router.get("/projects/{project}/segments")
async def list_segments(
    session: SessionDep,
    project: str,
    q: str | None = Query(None, description="Case-insensitive substring of the key or name."),
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[SegmentSummary]:
    items, next_cursor = await segments.list_segments(
        session, await get_project(session, project), q=q, cursor=cursor, limit=limit
    )
    return Page[SegmentSummary](items=items, next_cursor=next_cursor)


@router.post("/projects/{project}/segments", status_code=201)
async def create_segment(
    session: SessionDep, bus: ChangesDep, project: str, body: SegmentCreate
) -> SegmentOut:
    row = await segments.create_segment(session, await get_project(session, project), body)
    out = await segments.segment_out(session, row)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.get("/projects/{project}/segments/{segment}")
async def read_segment(session: SessionDep, project: str, segment: str) -> SegmentOut:
    row = await get_segment(session, await get_project(session, project), segment)
    return await segments.segment_out(session, row)


@router.put("/projects/{project}/segments/{segment}")
async def replace_segment_config(
    session: SessionDep, bus: ChangesDep, project: str, segment: str, body: SegmentConfigIn
) -> SegmentOut:
    row = await get_segment(session, await get_project(session, project), segment)
    await segments.replace_segment_config(session, row, body)
    out = await segments.segment_out(session, row)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.patch("/projects/{project}/segments/{segment}")
async def update_segment(
    session: SessionDep, bus: ChangesDep, project: str, segment: str, body: SegmentUpdate
) -> SegmentOut:
    row = await get_segment(session, await get_project(session, project), segment)
    await segments.update_segment(session, row, body)
    out = await segments.segment_out(session, row)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.delete("/projects/{project}/segments/{segment}", status_code=204)
async def delete_segment(
    session: SessionDep, bus: ChangesDep, project: str, segment: str
) -> Response:
    row = await get_segment(session, await get_project(session, project), segment)
    project_id = row.project_id
    await segments.delete_segment(session, row)
    await session.commit()
    await bus.publish(project_id)
    return Response(status_code=204)

from typing import Annotated

from fastapi import APIRouter, Query, Response

from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models.enums import FlagType
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.flags import (
    FlagCreate,
    FlagOut,
    FlagSummary,
    FlagUpdate,
    VariationIn,
    VariationOut,
    VariationUpdate,
)
from megooflags_api.services import flags, variations
from megooflags_api.services.lookup import get_flag, get_project, get_variation

router = APIRouter(tags=["flags"], responses=ERROR_RESPONSES)


@router.get("/projects/{project}/flags")
async def list_flags(
    session: SessionDep,
    project: str,
    q: str | None = Query(None, description="Case-insensitive substring of the key or name."),
    tag: Annotated[
        list[str] | None, Query(description="Repeatable: flags must have every tag given.")
    ] = None,
    owner: str | None = None,
    archived: bool = Query(False, description="List only archived flags instead of live ones."),
    type: FlagType | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[FlagSummary]:
    items, next_cursor = await flags.list_flags(
        session,
        await get_project(session, project),
        q=q,
        tags=tag or [],
        owner=owner,
        archived=archived,
        type=type,
        cursor=cursor,
        limit=limit,
    )
    return Page[FlagSummary](items=items, next_cursor=next_cursor)


@router.post("/projects/{project}/flags", status_code=201)
async def create_flag(session: SessionDep, project: str, body: FlagCreate) -> FlagOut:
    row = await flags.create_flag(session, await get_project(session, project), body)
    out = await flags.flag_out(session, row)
    await session.commit()
    return out


@router.get("/projects/{project}/flags/{flag}")
async def read_flag(session: SessionDep, project: str, flag: str) -> FlagOut:
    row = await get_flag(session, await get_project(session, project), flag)
    return await flags.flag_out(session, row)


@router.patch("/projects/{project}/flags/{flag}")
async def update_flag(session: SessionDep, project: str, flag: str, body: FlagUpdate) -> FlagOut:
    row = await get_flag(session, await get_project(session, project), flag)
    await flags.update_flag(session, row, body)
    out = await flags.flag_out(session, row)
    await session.commit()
    return out


@router.post("/projects/{project}/flags/{flag}/archive")
async def archive_flag(session: SessionDep, project: str, flag: str) -> FlagOut:
    row = await get_flag(session, await get_project(session, project), flag)
    await flags.archive_flag(session, row)
    out = await flags.flag_out(session, row)
    await session.commit()
    return out


@router.post("/projects/{project}/flags/{flag}/restore")
async def restore_flag(session: SessionDep, project: str, flag: str) -> FlagOut:
    row = await get_flag(session, await get_project(session, project), flag)
    await flags.restore_flag(session, row)
    out = await flags.flag_out(session, row)
    await session.commit()
    return out


@router.delete("/projects/{project}/flags/{flag}", status_code=204)
async def delete_flag(session: SessionDep, project: str, flag: str) -> Response:
    row = await get_flag(session, await get_project(session, project), flag)
    await flags.delete_flag(session, row)
    await session.commit()
    return Response(status_code=204)


@router.post("/projects/{project}/flags/{flag}/variations", status_code=201)
async def add_variation(
    session: SessionDep, project: str, flag: str, body: VariationIn
) -> VariationOut:
    flag_row = await get_flag(session, await get_project(session, project), flag)
    row = await variations.add_variation(session, flag_row, body)
    out = VariationOut.model_validate(row, from_attributes=True)
    await session.commit()
    return out


@router.patch("/projects/{project}/flags/{flag}/variations/{variation}")
async def update_variation(
    session: SessionDep, project: str, flag: str, variation: str, body: VariationUpdate
) -> VariationOut:
    flag_row = await get_flag(session, await get_project(session, project), flag)
    row = await get_variation(session, flag_row, variation)
    await variations.update_variation(session, flag_row, row, body)
    out = VariationOut.model_validate(row, from_attributes=True)
    await session.commit()
    return out


@router.delete("/projects/{project}/flags/{flag}/variations/{variation}", status_code=204)
async def delete_variation(
    session: SessionDep, project: str, flag: str, variation: str
) -> Response:
    flag_row = await get_flag(session, await get_project(session, project), flag)
    row = await get_variation(session, flag_row, variation)
    await variations.delete_variation(session, flag_row, row)
    await session.commit()
    return Response(status_code=204)

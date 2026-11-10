from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models import Project
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
    )
    return Page[FlagSummary](items=items, next_cursor=next_cursor)


@router.post("/projects/{project}/flags", status_code=201)
async def create_flag(
    session: SessionDep, authz: CreateFlag, bus: ChangesDep, project: str, body: FlagCreate
) -> FlagOut:
    row = await flags.create_flag(session, _project(authz), body)
    out = await flags.flag_out(session, row)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.get("/projects/{project}/flags/{flag}")
async def read_flag(session: SessionDep, authz: ReadProject, project: str, flag: str) -> FlagOut:
    row = await get_flag(session, _project(authz), flag)
    return await flags.flag_out(session, row)


@router.patch("/projects/{project}/flags/{flag}")
async def update_flag(
    session: SessionDep,
    authz: UpdateFlag,
    bus: ChangesDep,
    project: str,
    flag: str,
    body: FlagUpdate,
) -> FlagOut:
    row = await get_flag(session, _project(authz), flag)
    await flags.update_flag(session, row, body)
    out = await flags.flag_out(session, row)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.post("/projects/{project}/flags/{flag}/archive")
async def archive_flag(
    session: SessionDep, authz: ArchiveFlag, bus: ChangesDep, project: str, flag: str
) -> FlagOut:
    row = await get_flag(session, _project(authz), flag)
    await flags.archive_flag(session, row)
    out = await flags.flag_out(session, row)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.post("/projects/{project}/flags/{flag}/restore")
async def restore_flag(
    session: SessionDep, authz: ArchiveFlag, bus: ChangesDep, project: str, flag: str
) -> FlagOut:
    row = await get_flag(session, _project(authz), flag)
    await flags.restore_flag(session, row)
    out = await flags.flag_out(session, row)
    await session.commit()
    await bus.publish(row.project_id)
    return out


@router.delete("/projects/{project}/flags/{flag}", status_code=204)
async def delete_flag(
    session: SessionDep, authz: ArchiveFlag, bus: ChangesDep, project: str, flag: str
) -> Response:
    row = await get_flag(session, _project(authz), flag)
    project_id = row.project_id
    await flags.delete_flag(session, row)
    await session.commit()
    await bus.publish(project_id)
    return Response(status_code=204)


@router.post("/projects/{project}/flags/{flag}/variations", status_code=201)
async def add_variation(
    session: SessionDep,
    authz: UpdateFlag,
    bus: ChangesDep,
    project: str,
    flag: str,
    body: VariationIn,
) -> VariationOut:
    flag_row = await get_flag(session, _project(authz), flag)
    row = await variations.add_variation(session, flag_row, body)
    out = VariationOut.model_validate(row, from_attributes=True)
    await session.commit()
    await bus.publish(flag_row.project_id)
    return out


@router.patch("/projects/{project}/flags/{flag}/variations/{variation}")
async def update_variation(
    session: SessionDep,
    authz: UpdateFlag,
    bus: ChangesDep,
    project: str,
    flag: str,
    variation: str,
    body: VariationUpdate,
) -> VariationOut:
    flag_row = await get_flag(session, _project(authz), flag)
    row = await get_variation(session, flag_row, variation)
    await variations.update_variation(session, flag_row, row, body)
    out = VariationOut.model_validate(row, from_attributes=True)
    await session.commit()
    await bus.publish(flag_row.project_id)
    return out


@router.delete("/projects/{project}/flags/{flag}/variations/{variation}", status_code=204)
async def delete_variation(
    session: SessionDep, authz: UpdateFlag, bus: ChangesDep, project: str, flag: str, variation: str
) -> Response:
    flag_row = await get_flag(session, _project(authz), flag)
    row = await get_variation(session, flag_row, variation)
    await variations.delete_variation(session, flag_row, row)
    await session.commit()
    await bus.publish(flag_row.project_id)
    return Response(status_code=204)

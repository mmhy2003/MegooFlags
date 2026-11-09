from fastapi import APIRouter

from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.schemas.flags import FlagCreate, FlagOut
from megooflags_api.services import flags
from megooflags_api.services.lookup import get_flag, get_project

router = APIRouter(tags=["flags"], responses=ERROR_RESPONSES)


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

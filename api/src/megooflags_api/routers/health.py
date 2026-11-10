import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from megooflags_api import __version__
from megooflags_api.settings import Settings
from megooflags_api.valkey import valkey_ready

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadyResponse(BaseModel):
    status: str


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=ReadyResponse, responses={503: {"model": ReadyResponse}})
async def readyz(request: Request) -> ReadyResponse | JSONResponse:
    db_ok, valkey_ok = await asyncio.gather(
        _db_ready(request.app.state.engine, request.app.state.settings),
        valkey_ready(request.app.state.valkey),
    )
    if not (db_ok and valkey_ok):
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return ReadyResponse(status="ready")


async def _db_ready(engine: AsyncEngine, settings: Settings) -> bool:
    try:
        async with asyncio.timeout(settings.db_connect_timeout + 1):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True

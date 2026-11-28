from typing import Annotated

from fastapi import APIRouter, Depends, Request

from megooflags_api import __version__
from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, authenticated
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.schemas.system import SystemVersionOut, UpdateOut
from megooflags_api.services.updates import available_update, release_url

router = APIRouter(prefix="/system", tags=["system"], responses=ERROR_RESPONSES)

Caller = Annotated[Authz, Depends(authenticated())]


@router.get("/version")
async def system_version(request: Request, session: SessionDep, authz: Caller) -> SystemVersionOut:
    """The running version, and a newer release for org admins when the update check is on."""
    update = None
    if request.app.state.settings.update_check and authz.authorizer.can(Permission.ORG_MANAGE):
        latest = await available_update(session, __version__)
        if latest is not None:
            update = UpdateOut(version=latest, url=release_url(latest))
    return SystemVersionOut(version=__version__, update=update)

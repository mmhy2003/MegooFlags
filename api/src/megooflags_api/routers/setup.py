"""First-run setup: while no user exists, the first visitor creates the first administrator."""

from fastapi import APIRouter, Request, Response
from sqlalchemy import select, text

from megooflags_api.db import SessionDep
from megooflags_api.errors import ErrorBody, already_set_up
from megooflags_api.models import Role, RoleBinding
from megooflags_api.models.enums import ScopeKind, SubjectKind
from megooflags_api.routers.auth import session_principal, set_session_cookie, start_session
from megooflags_api.schemas.auth import SetupIn, SetupStatus, SignInOut
from megooflags_api.services.audit import record, request_meta, snapshot
from megooflags_api.services.users import create_user, has_users, user_out

router = APIRouter(
    prefix="/setup",
    tags=["setup"],
    responses={409: {"model": ErrorBody}, 422: {"model": ErrorBody}},
)

# "MFSETUP1": serialises concurrent setups, so exactly one of them finds no user.
SETUP_LOCK = 0x4D46_5345_5455_5031


@router.get("/status")
async def setup_status(session: SessionDep) -> SetupStatus:
    return SetupStatus(needs_setup=not await has_users(session))


@router.post("", status_code=201)
async def setup(
    session: SessionDep, request: Request, response: Response, body: SetupIn
) -> SignInOut:
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SETUP_LOCK})
    if await has_users(session):
        raise already_set_up()
    user = await create_user(session, body.email, body.name, body.password)
    admin_id = await session.scalar(select(Role.id).where(Role.key == "admin"))
    session.add(
        RoleBinding(
            subject_kind=SubjectKind.USER, subject_id=user.id, role_id=admin_id, scope=ScopeKind.ORG
        )
    )
    row, token = await start_session(session, request, user, auth_method="password")
    await record(
        session,
        session_principal(user, row),
        request_meta(request),
        "auth.setup",
        "user",
        user.email,
        after=snapshot(user_out(user)),
    )
    await session.commit()
    set_session_cookie(response, request, token)
    return SignInOut(status="signed_in")

import uuid

from fastapi import APIRouter, Response

from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.schemas.sdk_keys import SdkKeyCreate, SdkKeyCreated, SdkKeyOut
from megooflags_api.services import sdk_keys
from megooflags_api.services.lookup import get_environment, get_project

router = APIRouter(tags=["sdk-keys"], responses=ERROR_RESPONSES)


@router.post("/projects/{project}/environments/{env}/sdk-keys", status_code=201)
async def create_sdk_key(
    session: SessionDep, project: str, env: str, body: SdkKeyCreate
) -> SdkKeyCreated:
    environment = await get_environment(session, await get_project(session, project), env)
    row, key = await sdk_keys.create_key(session, environment, body)
    out = SdkKeyCreated(
        id=row.id,
        name=row.name,
        kind=row.kind,
        prefix=row.prefix,
        created_at=row.created_at,
        key=key,
    )
    await session.commit()
    return out


@router.get("/projects/{project}/environments/{env}/sdk-keys")
async def list_sdk_keys(session: SessionDep, project: str, env: str) -> list[SdkKeyOut]:
    environment = await get_environment(session, await get_project(session, project), env)
    rows = await sdk_keys.list_keys(session, environment)
    return [SdkKeyOut.model_validate(k, from_attributes=True) for k in rows]


@router.delete("/projects/{project}/environments/{env}/sdk-keys/{id}", status_code=204)
async def revoke_sdk_key(session: SessionDep, project: str, env: str, id: uuid.UUID) -> Response:
    environment = await get_environment(session, await get_project(session, project), env)
    await sdk_keys.revoke_key(session, environment, id)
    await session.commit()
    return Response(status_code=204)

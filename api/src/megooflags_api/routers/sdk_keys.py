import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models import Environment
from megooflags_api.schemas.sdk_keys import SdkKeyCreate, SdkKeyCreated, SdkKeyOut
from megooflags_api.services import sdk_keys
from megooflags_api.services.audit import AuditDep, snapshot
from megooflags_api.services.changes import ChangesDep

router = APIRouter(tags=["sdk-keys"], responses=ERROR_RESPONSES)

ReadKeys = Annotated[Authz, Depends(require(Permission.SDK_KEY_READ))]
ManageKeys = Annotated[Authz, Depends(require(Permission.SDK_KEY_MANAGE))]


def _environment(authz: Authz) -> Environment:
    assert authz.environment is not None  # every route here has {env}
    return authz.environment


@router.post("/projects/{project}/environments/{env}/sdk-keys", status_code=201)
async def create_sdk_key(
    session: SessionDep,
    authz: ManageKeys,
    audit: AuditDep,
    project: str,
    env: str,
    body: SdkKeyCreate,
) -> SdkKeyCreated:
    environment = _environment(authz)
    row, key = await sdk_keys.create_key(session, environment, body)
    out = SdkKeyCreated(
        id=row.id,
        name=row.name,
        kind=row.kind,
        prefix=row.prefix,
        created_at=row.created_at,
        key=key,
    )
    # The snapshot is SdkKeyOut, so the secret is never stored.
    await audit.record(
        "sdk_key.create",
        "sdk_key",
        str(row.id),
        project_id=environment.project_id,
        environment_id=environment.id,
        after=snapshot(SdkKeyOut.model_validate(row, from_attributes=True)),
    )
    await session.commit()
    return out


@router.get("/projects/{project}/environments/{env}/sdk-keys")
async def list_sdk_keys(
    session: SessionDep, authz: ReadKeys, project: str, env: str
) -> list[SdkKeyOut]:
    environment = _environment(authz)
    rows = await sdk_keys.list_keys(session, environment)
    return [SdkKeyOut.model_validate(k, from_attributes=True) for k in rows]


@router.delete("/projects/{project}/environments/{env}/sdk-keys/{id}", status_code=204)
async def revoke_sdk_key(
    session: SessionDep,
    authz: ManageKeys,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    env: str,
    id: uuid.UUID,
) -> Response:
    environment = _environment(authz)
    key = await sdk_keys.get_key(session, environment, id)
    before = SdkKeyOut.model_validate(key, from_attributes=True)
    await sdk_keys.revoke_key(session, key)
    await audit.record(
        "sdk_key.revoke",
        "sdk_key",
        str(key.id),
        project_id=environment.project_id,
        environment_id=environment.id,
        before=snapshot(before),
        after=snapshot(SdkKeyOut.model_validate(key, from_attributes=True)),
    )
    await session.commit()
    await bus.publish_key_revoked(key.id)
    return Response(status_code=204)

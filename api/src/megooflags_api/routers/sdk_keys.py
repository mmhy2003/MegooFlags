import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models import Environment, SdkKey
from megooflags_api.schemas.common import RotateIn
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


def _out(row: SdkKey) -> SdkKeyOut:
    return SdkKeyOut.model_validate(row, from_attributes=True)


def _created(row: SdkKey, key: str) -> SdkKeyCreated:
    return SdkKeyCreated.model_validate(_out(row).model_dump(exclude={"revoked_at"}) | {"key": key})


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
    out = _created(row, key)
    # The snapshot is SdkKeyOut, so the secret is never stored.
    await audit.record(
        "sdk_key.create",
        "sdk_key",
        str(row.id),
        project_id=environment.project_id,
        environment_id=environment.id,
        after=snapshot(_out(row)),
    )
    await session.commit()
    return out


@router.get("/projects/{project}/environments/{env}/sdk-keys")
async def list_sdk_keys(
    session: SessionDep, authz: ReadKeys, project: str, env: str
) -> list[SdkKeyOut]:
    environment = _environment(authz)
    rows = await sdk_keys.list_keys(session, environment)
    return [_out(k) for k in rows]


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
    before = _out(key)
    await sdk_keys.revoke_key(session, key)
    await audit.record(
        "sdk_key.revoke",
        "sdk_key",
        str(key.id),
        project_id=environment.project_id,
        environment_id=environment.id,
        before=snapshot(before),
        after=snapshot(_out(key)),
    )
    await session.commit()
    await bus.publish_key_revoked(key.id)
    return Response(status_code=204)


@router.post("/projects/{project}/environments/{env}/sdk-keys/{id}/rotate", status_code=201)
async def rotate_sdk_key(
    session: SessionDep,
    authz: ManageKeys,
    audit: AuditDep,
    bus: ChangesDep,
    project: str,
    env: str,
    id: uuid.UUID,
    body: RotateIn,
) -> SdkKeyCreated:
    """Replace the key with a new one of the same kind and name. The old key keeps working for
    `overlap_seconds`; 0 revokes it at once. The new secret is returned only here."""
    environment = _environment(authz)
    before = _out(await sdk_keys.get_key(session, environment, id, for_update=True))
    old, new, key = await sdk_keys.rotate_key(
        session, environment, id, timedelta(seconds=body.overlap_seconds)
    )
    out = _created(new, key)
    await audit.record(
        "sdk_key.rotate",
        "sdk_key",
        str(old.id),
        project_id=environment.project_id,
        environment_id=environment.id,
        before={"old": snapshot(before)},
        after={"old": snapshot(_out(old)), "new": snapshot(_out(new))},
    )
    await session.commit()
    if old.revoked_at is not None:
        await bus.publish_key_revoked(old.id)
    return out

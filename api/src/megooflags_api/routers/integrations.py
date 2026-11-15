import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.crypto import SecretBox, SecretBoxDep
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models import Project
from megooflags_api.schemas.integrations import (
    IntegrationCreate,
    IntegrationCreated,
    IntegrationOut,
    IntegrationSecret,
    IntegrationUpdate,
)
from megooflags_api.services.audit import AuditDep, snapshot
from megooflags_api.services.integrations import crud
from megooflags_api.settings import Settings

router = APIRouter(tags=["integrations"], responses=ERROR_RESPONSES)

Manage = Annotated[Authz, Depends(require(Permission.PROJECT_MANAGE))]

BASE = "/projects/{project}/integrations"


def _project(authz: Authz) -> Project:
    assert authz.project is not None  # every route here has {project}
    return authz.project


def _box(box: SecretBox | None) -> SecretBox:
    assert box is not None, "the lifespan refuses to start without MEGOOFLAGS_SECRET_KEY"
    return box


def _allow_private(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    return settings.webhook_allow_private


@router.get(BASE)
async def list_integrations(
    session: SessionDep, authz: Manage, project: str
) -> list[IntegrationOut]:
    """The project's webhook, Slack and Teams integrations, by name."""
    return await crud.list_integrations(session, _project(authz))


@router.post(BASE, status_code=201)
async def create_integration(
    session: SessionDep,
    authz: Manage,
    audit: AuditDep,
    box: SecretBoxDep,
    request: Request,
    project: str,
    body: IntegrationCreate,
) -> IntegrationCreated:
    """Add an integration. The URL must be https on a public address (422 at `url`). A webhook's
    signing secret is returned only here and by rotate-secret."""
    project_row = _project(authz)
    row, secret = await crud.create_integration(
        session,
        project_row,
        body,
        _box(box),
        authz.authorizer,
        allow_private=_allow_private(request),
    )
    out = await crud.integration_out(session, row)
    # The snapshot is IntegrationOut: no URL and no secret.
    await audit.record(
        "integration.create",
        "integration",
        str(row.id),
        project_id=project_row.id,
        after=snapshot(out),
    )
    await session.commit()
    return IntegrationCreated.model_validate(out.model_dump() | {"secret": secret})


@router.get(f"{BASE}/{{id}}")
async def read_integration(
    session: SessionDep, authz: Manage, project: str, id: uuid.UUID
) -> IntegrationOut:
    row = await crud.get_integration(session, _project(authz), id)
    return await crud.integration_out(session, row)


@router.patch(f"{BASE}/{{id}}")
async def update_integration(
    session: SessionDep,
    authz: Manage,
    audit: AuditDep,
    box: SecretBoxDep,
    request: Request,
    project: str,
    id: uuid.UUID,
    body: IntegrationUpdate,
) -> IntegrationOut:
    """Change any of the fields; the kind cannot change."""
    project_row = _project(authz)
    row = await crud.get_integration(session, project_row, id, for_update=True)
    before = await crud.integration_out(session, row)
    await crud.update_integration(
        session,
        project_row,
        row,
        body,
        _box(box),
        authz.authorizer,
        allow_private=_allow_private(request),
    )
    out = await crud.integration_out(session, row)
    await audit.record(
        "integration.update",
        "integration",
        str(row.id),
        project_id=project_row.id,
        before=snapshot(before),
        after=snapshot(out),
    )
    await session.commit()
    return out


@router.delete(f"{BASE}/{{id}}", status_code=204)
async def delete_integration(
    session: SessionDep, authz: Manage, audit: AuditDep, project: str, id: uuid.UUID
) -> Response:
    """Delete the integration and its delivery log."""
    project_row = _project(authz)
    row = await crud.get_integration(session, project_row, id, for_update=True)
    before = await crud.integration_out(session, row)
    await crud.delete_integration(session, row)
    await audit.record(
        "integration.delete",
        "integration",
        str(id),
        project_id=project_row.id,
        before=snapshot(before),
    )
    await session.commit()
    return Response(status_code=204)


@router.post(f"{BASE}/{{id}}/rotate-secret")
async def rotate_integration_secret(
    session: SessionDep,
    authz: Manage,
    audit: AuditDep,
    box: SecretBoxDep,
    project: str,
    id: uuid.UUID,
) -> IntegrationSecret:
    """Replace a webhook's signing secret; the old one stops working at once. Slack and Teams
    integrations have none (422 at `kind`). The new secret is returned only here."""
    project_row = _project(authz)
    row = await crud.get_integration(session, project_row, id, for_update=True)
    before = await crud.integration_out(session, row)
    secret = await crud.rotate_secret(session, row, _box(box))
    await audit.record(
        "integration.rotate_secret",
        "integration",
        str(row.id),
        project_id=project_row.id,
        before=snapshot(before),
        after=snapshot(await crud.integration_out(session, row)),
    )
    await session.commit()
    return IntegrationSecret(secret=secret)

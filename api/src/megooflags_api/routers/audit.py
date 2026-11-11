"""The audit log (`/audit`) and its export (`/audit/export`), scoped by the caller's bindings."""

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES, not_found, validation_error
from megooflags_api.schemas.audit import AuditEntryOut
from megooflags_api.schemas.common import Page
from megooflags_api.services import audit
from megooflags_api.services.audit import AuditFilters
from megooflags_api.services.lookup import get_environment, get_project

router = APIRouter(prefix="/audit", tags=["audit"], responses=ERROR_RESPONSES)

ReadAudit = Annotated[Authz, Depends(require(Permission.AUDIT_READ, anywhere=True))]

CSV_COLUMNS = [
    "id",
    "occurred_at",
    "actor_kind",
    "actor_id",
    "actor_label",
    "auth_method",
    "ip",
    "user_agent",
    "request_id",
    "action",
    "resource_type",
    "resource_key",
    "project",
    "environment",
    "before",
    "after",
]
# A spreadsheet treats a cell starting with one of these as a formula (OWASP CSV injection).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _scope(authz: Authz) -> set[uuid.UUID] | None:
    """The projects whose entries the caller may read; None means every entry."""
    return authz.authorizer.projects_with(Permission.AUDIT_READ)


async def get_filters(
    session: SessionDep,
    authz: ReadAudit,
    actor: Annotated[str | None, Query(description="An actor id, or an exact label.")] = None,
    action: str | None = None,
    resource_type: str | None = None,
    project: Annotated[str | None, Query(description="A project key.")] = None,
    env: Annotated[str | None, Query(description="An environment key; needs `project`.")] = None,
    occurred_from: Annotated[datetime | None, Query(alias="from", description="Inclusive.")] = None,
    occurred_to: Annotated[datetime | None, Query(alias="to", description="Exclusive.")] = None,
) -> AuditFilters:
    """404 for a project or environment that is unknown or outside the caller's scope."""
    project_id = environment_id = None
    if env is not None and project is None:
        raise validation_error("env needs project", "env")
    if project is not None:
        row = await get_project(session, project)
        scope = _scope(authz)
        if scope is not None and row.id not in scope:
            raise not_found("project", project)
        project_id = row.id
        if env is not None:
            environment_id = (await get_environment(session, row, env)).id
    return AuditFilters(
        actor=actor,
        action=action,
        resource_type=resource_type,
        project_id=project_id,
        environment_id=environment_id,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )


Filters = Annotated[AuditFilters, Depends(get_filters)]


@router.get("")
async def list_entries(
    session: SessionDep,
    authz: ReadAudit,
    filters: Filters,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[AuditEntryOut]:
    """Newest first. Without org-wide `audit.read`, only entries of the caller's projects."""
    items, next_cursor = await audit.query(session, filters, _scope(authz), cursor, limit)
    return Page[AuditEntryOut](items=items, next_cursor=next_cursor)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def _csv_row(entry: AuditEntryOut) -> list[str]:
    values = [
        entry.id,
        entry.occurred_at.isoformat(),
        entry.actor.kind,
        entry.actor.id,
        entry.actor.label,
        entry.actor.auth_method,
        entry.ip,
        entry.user_agent,
        entry.request_id,
        entry.action,
        entry.resource_type,
        entry.resource_key,
        entry.project,
        entry.environment,
        None if entry.before is None else json.dumps(entry.before),
        None if entry.after is None else json.dumps(entry.after),
    ]
    return [_cell(value) for value in values]


def _csv_chunk(rows: Iterable[list[str]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return buffer.getvalue()


@router.get(
    "/export",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/csv": {}, "application/x-ndjson": {}},
            "description": "Every matching entry, newest first, as an attachment.",
        }
    },
)
async def export_entries(
    session: SessionDep,
    authz: ReadAudit,
    filters: Filters,
    format: Literal["csv", "ndjson"] = "csv",
) -> StreamingResponse:
    """The same entries and filters as `GET /audit`, without paging. CSV cells that a
    spreadsheet would run as a formula get a leading `'`."""
    batches = audit.iter_export(session, filters, _scope(authz))

    async def csv_body() -> AsyncIterator[str]:
        yield _csv_chunk([CSV_COLUMNS])
        async for batch in batches:
            yield _csv_chunk(_csv_row(entry) for entry in batch)

    async def ndjson_body() -> AsyncIterator[str]:
        async for batch in batches:
            yield "".join(entry.model_dump_json() + "\n" for entry in batch)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    headers = {"Content-Disposition": f'attachment; filename="audit-{stamp}.{format}"'}
    if format == "csv":
        return StreamingResponse(csv_body(), media_type="text/csv", headers=headers)
    return StreamingResponse(ndjson_body(), media_type="application/x-ndjson", headers=headers)

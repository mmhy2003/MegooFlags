"""The audit log: who changed what, when, from where, with before and after snapshots.

Entries are written in the change's own transaction and are never updated or deleted.
"""

import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Protocol

from fastapi import Depends, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import RowMapping, Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.principal import Principal
from megooflags_api.db import SessionDep
from megooflags_api.errors import validation_error
from megooflags_api.models import AuditEntry, Environment, Flag, Project
from megooflags_api.schemas.audit import AuditActor, AuditEntryOut
from megooflags_api.services.pagination import decode_cursor, page_slice

SECRET_FIELDS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "password_hash",
        "secret",
        "secret_hash",
        "secret_encrypted",
        "token",
        "token_hash",
        "client_secret",
        "sp_private_key",
        "totp_secret",
        "code",
        "code_hash",
        "recovery_codes",
        "credential",
        "public_key",
    }
)
REDACTED = "***"
# Rows fetched per query while exporting.
EXPORT_BATCH = 1000


@dataclass(frozen=True)
class RequestMeta:
    ip: str | None
    user_agent: str | None
    request_id: str | None


def request_meta(request: Request) -> RequestMeta:
    """Client IP (already taken from trusted forwarded headers), user agent and request id."""
    user_agent = request.headers.get("user-agent")
    request_id = request.headers.get("x-request-id")
    return RequestMeta(
        ip=request.client.host if request.client else None,
        user_agent=None if user_agent is None else user_agent[:512],
        request_id=None if request_id is None else request_id[:128],
    )


def redact(value: Any) -> Any:
    """A copy of `value` with every secret field, at any depth, replaced by "***"."""
    if isinstance(value, Mapping):
        return {k: REDACTED if k in SECRET_FIELDS else redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


def snapshot(value: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    """A JSON-ready dict of a schema model or a mapping, for `before`/`after`."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(jsonable_encoder(value))


async def record(
    session: AsyncSession,
    principal: Principal | None,
    meta: RequestMeta,
    action: str,
    resource_type: str,
    resource_key: str,
    *,
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    actor_label: str | None = None,
) -> AuditEntry:
    """Adds an entry to the caller's transaction and flushes it; the caller commits.

    Without a principal the actor is "anonymous", labelled `actor_label` (such as the email
    given at a failed sign-in).
    """
    if principal is None:
        actor_kind, actor_id, label, auth_method = "anonymous", None, "anonymous", None
    else:
        actor_kind, actor_id = principal.kind.value, principal.id
        label, auth_method = principal.label, principal.auth_method
    entry = AuditEntry(
        actor_kind=actor_kind,
        actor_id=actor_id,
        actor_label=actor_label if actor_label is not None else label,
        auth_method=auth_method,
        ip=meta.ip,
        user_agent=meta.user_agent,
        request_id=meta.request_id,
        action=action,
        resource_type=resource_type,
        resource_key=resource_key,
        project_id=project_id,
        environment_id=environment_id,
        before=None if before is None else redact(snapshot(before)),
        after=None if after is None else redact(snapshot(after)),
    )
    session.add(entry)
    await session.flush()
    return entry


class Auditor:
    """`record` bound to the request: its session, metadata and `request.state.principal`."""

    def __init__(self, session: AsyncSession, request: Request) -> None:
        self.session = session
        self.request = request

    async def record(
        self, action: str, resource_type: str, resource_key: str, **kw: Any
    ) -> AuditEntry:
        principal: Principal | None = getattr(self.request.state, "principal", None)
        return await record(
            self.session,
            principal,
            request_meta(self.request),
            action,
            resource_type,
            resource_key,
            **kw,
        )


class AuditSink(Protocol):
    """Where a service writes its audit entries: the request's `Auditor`, or a `SystemAuditor`."""

    async def record(
        self, action: str, resource_type: str, resource_key: str, **kw: Any
    ) -> AuditEntry: ...


@dataclass(frozen=True)
class Actor:
    """Who started a change, kept on rows that act later (schedules, rollouts, change requests)."""

    id: uuid.UUID | None
    label: str

    @classmethod
    def of(cls, principal: Principal) -> "Actor":
        return cls(principal.actor_id, principal.label)


SCHEDULER = Actor(None, "scheduler")


async def record_system(
    session: AsyncSession,
    action: str,
    resource_type: str,
    resource_key: str,
    *,
    label: str = "scheduler",
    project_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
) -> AuditEntry:
    """Like `record`, for a background job: actor kind "system", no actor id or request data."""
    entry = AuditEntry(
        actor_kind="system",
        actor_id=None,
        actor_label=label,
        auth_method=None,
        ip=None,
        user_agent=None,
        request_id=None,
        action=action,
        resource_type=resource_type,
        resource_key=resource_key,
        project_id=project_id,
        environment_id=environment_id,
        before=None if before is None else redact(snapshot(before)),
        after=None if after is None else redact(snapshot(after)),
    )
    session.add(entry)
    await session.flush()
    return entry


class SystemAuditor:
    """`record_system` bound to a job's session and label."""

    def __init__(self, session: AsyncSession, label: str = "scheduler") -> None:
        self.session = session
        self.label = label

    async def record(
        self, action: str, resource_type: str, resource_key: str, **kw: Any
    ) -> AuditEntry:
        return await record_system(
            self.session, action, resource_type, resource_key, label=self.label, **kw
        )


async def record_flag_change(
    audit: AuditSink,
    action: str,
    flag: Flag,
    env: Environment,
    before: BaseModel | Mapping[str, Any],
    after: BaseModel | Mapping[str, Any],
    **context: Any,
) -> AuditEntry:
    """A change to `flag` in `env`; `context` (such as schedule_id or created_by) is merged into
    `after`."""
    return await audit.record(
        action,
        "flag",
        flag.key,
        project_id=env.project_id,
        environment_id=env.id,
        before=snapshot(before),
        after={**snapshot(after), **context},
    )


async def get_auditor(session: SessionDep, request: Request) -> Auditor:
    return Auditor(session, request)


AuditDep = Annotated[Auditor, Depends(get_auditor)]


@dataclass(frozen=True)
class AuditFilters:
    """`actor` is an actor id or an exact label; `occurred_from` is inclusive, `occurred_to`
    exclusive."""

    actor: str | None = None
    action: str | None = None
    resource_type: str | None = None
    project_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


def _entry_out(row: RowMapping) -> AuditEntryOut:
    return AuditEntryOut(
        id=row["id"],
        occurred_at=row["occurred_at"],
        actor=AuditActor(
            kind=row["actor_kind"],
            id=row["actor_id"],
            label=row["actor_label"],
            auth_method=row["auth_method"],
        ),
        ip=row["ip"],
        user_agent=row["user_agent"],
        request_id=row["request_id"],
        action=row["action"],
        resource_type=row["resource_type"],
        resource_key=row["resource_key"],
        project=row["project"],
        environment=row["environment"],
        before=row["before"],
        after=row["after"],
    )


def _select(filters: AuditFilters, scope: set[uuid.UUID] | None) -> Select[Any]:
    """Newest first; entries outside `scope` (None means org-wide) are left out, and so are
    org-level entries when there is a scope. Plain columns, so exports don't fill the session."""
    query = (
        select(
            *AuditEntry.__table__.columns,
            Project.key.label("project"),
            Environment.key.label("environment"),
        )
        .outerjoin(Project, Project.id == AuditEntry.project_id)
        .outerjoin(Environment, Environment.id == AuditEntry.environment_id)
        .order_by(AuditEntry.id.desc())
    )
    if scope is not None:
        query = query.where(AuditEntry.project_id.in_(scope))
    if filters.actor is not None:
        try:
            query = query.where(AuditEntry.actor_id == uuid.UUID(filters.actor))
        except ValueError:
            query = query.where(AuditEntry.actor_label == filters.actor)
    if filters.action is not None:
        query = query.where(AuditEntry.action == filters.action)
    if filters.resource_type is not None:
        query = query.where(AuditEntry.resource_type == filters.resource_type)
    if filters.project_id is not None:
        query = query.where(AuditEntry.project_id == filters.project_id)
    if filters.environment_id is not None:
        query = query.where(AuditEntry.environment_id == filters.environment_id)
    if filters.occurred_from is not None:
        query = query.where(AuditEntry.occurred_at >= filters.occurred_from)
    if filters.occurred_to is not None:
        query = query.where(AuditEntry.occurred_at < filters.occurred_to)
    return query


def _cursor_id(cursor: str) -> int:
    key = decode_cursor(cursor)
    if not (key.isascii() and key.isdigit()):
        raise validation_error("invalid cursor", "cursor")
    return int(key)


async def query(
    session: AsyncSession,
    filters: AuditFilters,
    scope: set[uuid.UUID] | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[AuditEntryOut], str | None]:
    """One page, newest first, keyed on the id."""
    statement = _select(filters, scope).limit(limit + 1)
    if cursor is not None:
        statement = statement.where(AuditEntry.id < _cursor_id(cursor))
    rows = list((await session.execute(statement)).mappings())
    page, next_cursor = page_slice(rows, limit, lambda row: str(row["id"]))
    return [_entry_out(row) for row in page], next_cursor


async def iter_export(
    session: AsyncSession, filters: AuditFilters, scope: set[uuid.UUID] | None
) -> AsyncIterator[list[AuditEntryOut]]:
    """Every matching entry, newest first, in batches of EXPORT_BATCH."""
    last_id: int | None = None
    while True:
        statement = _select(filters, scope).limit(EXPORT_BATCH)
        if last_id is not None:
            statement = statement.where(AuditEntry.id < last_id)
        rows = list((await session.execute(statement)).mappings())
        if not rows:
            return
        yield [_entry_out(row) for row in rows]
        if len(rows) < EXPORT_BATCH:
            return
        last_id = rows[-1]["id"]

"""The audit log: who changed what, when, from where, with before and after snapshots.

Entries are written in the change's own transaction and are never updated or deleted.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.principal import Principal
from megooflags_api.db import SessionDep
from megooflags_api.models import AuditEntry

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


async def get_auditor(session: SessionDep, request: Request) -> Auditor:
    return Auditor(session, request)


AuditDep = Annotated[Auditor, Depends(get_auditor)]

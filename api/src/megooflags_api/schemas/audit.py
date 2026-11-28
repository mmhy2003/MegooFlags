import uuid
from datetime import datetime
from typing import Any

from megooflags_api.schemas.common import ApiModel


class AuditActor(ApiModel):
    kind: str
    id: uuid.UUID | None
    label: str
    auth_method: str | None


class AuditEntryOut(ApiModel):
    """`project` and `environment` are keys; null once that project or environment is deleted."""

    id: int
    occurred_at: datetime
    actor: AuditActor
    ip: str | None
    user_agent: str | None
    request_id: str | None
    action: str
    resource_type: str
    resource_key: str
    project: str | None
    environment: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None

import uuid
from datetime import datetime

from pydantic import Field, JsonValue

from megooflags_api.models.enums import DeliveryStatus, EventGroup, IntegrationKind
from megooflags_api.schemas.common import ApiModel, Key, Name


class IntegrationCreate(ApiModel):
    kind: IntegrationKind
    name: Name
    url: str = Field(
        min_length=8,
        max_length=2000,
        description="Write-only: reads return `url_hint`. Slack URLs must be on hooks.slack.com.",
    )
    event_groups: list[EventGroup] = Field(min_length=1)
    all_environments: bool = True
    environments: list[Key] = Field(
        default=[], description="Environment keys; used when `all_environments` is false."
    )
    enabled: bool = True


class IntegrationUpdate(ApiModel):
    name: Name | None = None
    url: str | None = Field(None, min_length=8, max_length=2000)
    event_groups: list[EventGroup] | None = Field(None, min_length=1)
    all_environments: bool | None = None
    environments: list[Key] | None = None
    enabled: bool | None = None


class IntegrationOut(ApiModel):
    id: uuid.UUID
    kind: IntegrationKind
    name: str
    url_hint: str = Field(description="The scheme, host and last characters of the URL.")
    event_groups: list[EventGroup]
    all_environments: bool
    environments: list[str]
    enabled: bool
    last_delivery_at: datetime | None
    last_delivery_ok: bool | None
    created_at: datetime
    updated_at: datetime


class IntegrationCreated(IntegrationOut):
    secret: str | None = Field(description="Webhook signing secret. Shown only once.")


class IntegrationSecret(ApiModel):
    secret: str = Field(description="The new webhook signing secret. Shown only once.")


class DeliveryOut(ApiModel):
    id: uuid.UUID
    event_id: str = Field(description="The event's id; a delivery sent again keeps it.")
    event_type: str
    status: DeliveryStatus
    attempts: int
    next_attempt_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    response_body: str | None = Field(description="The first 2048 characters of the response.")
    duration_ms: int | None
    created_at: datetime
    delivered_at: datetime | None
    request_body: JsonValue = Field(description="The body sent for the integration's kind.")

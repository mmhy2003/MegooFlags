import uuid
from datetime import datetime

from pydantic import Field

from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.schemas.common import ApiModel, Name


class SdkKeyCreate(ApiModel):
    name: Name
    kind: SdkKeyKind


class _SdkKeyBase(ApiModel):
    id: uuid.UUID
    name: str
    kind: SdkKeyKind
    prefix: str
    created_at: datetime


class SdkKeyOut(_SdkKeyBase):
    revoked_at: datetime | None


class SdkKeyCreated(_SdkKeyBase):
    key: str = Field(description="The secret. Shown only once.")

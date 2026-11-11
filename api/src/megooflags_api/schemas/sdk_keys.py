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
    expires_at: datetime | None = Field(
        description="Set by a rotation with an overlap: the key stops working at this time."
    )
    rotated_from_id: uuid.UUID | None


class SdkKeyOut(_SdkKeyBase):
    revoked_at: datetime | None


class SdkKeyCreated(_SdkKeyBase):
    key: str = Field(description="The secret. Shown only once.")

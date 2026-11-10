import uuid
from datetime import datetime

from megooflags_api.models.enums import UserStatus
from megooflags_api.schemas.common import ApiModel


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    name: str
    status: UserStatus
    has_password: bool
    created_at: datetime
    last_login_at: datetime | None

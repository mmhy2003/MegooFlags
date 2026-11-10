from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

KEY_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,99}$"
Key = Annotated[str, StringConstraints(pattern=KEY_PATTERN)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(max_length=10000)]
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$"
    ),
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page[T](ApiModel):
    items: list[T]
    next_cursor: str | None


class FlagEnvironmentState(ApiModel):
    environment: str
    enabled: bool
    version: int
    updated_at: datetime

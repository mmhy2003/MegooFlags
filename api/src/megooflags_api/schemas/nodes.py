import uuid
from datetime import datetime
from typing import Annotated

from pydantic import Field, StringConstraints

from megooflags_api.models.enums import NodeStatus
from megooflags_api.schemas.common import ApiModel


class NodeOut(ApiModel):
    id: uuid.UUID
    name: str
    status: NodeStatus
    syncing: bool = Field(description="Healthy, but not yet on the current snapshot version.")
    created_at: datetime
    last_heartbeat_at: datetime | None
    synced_at: datetime | None
    version: str | None
    snapshot_version: int | None
    sdk_streams: int
    buffered_events: int
    address: str | None
    refused_version: str | None
    token_prefix: str | None = Field(description="The prefix of the newest active token.")


class NodeList(ApiModel):
    version: str = Field(description="The control plane's version.")
    snapshot_version: int = Field(description="The newest snapshot version published.")
    nodes: list[NodeOut]


class NodeCreate(ApiModel):
    name: Annotated[
        str,
        StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
    ]


class NodeCreated(NodeOut):
    token: str = Field(description="The join token. Shown only once.")

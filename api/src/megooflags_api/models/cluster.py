"""Cluster: worker nodes, their tokens, published environment snapshots and event batches."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Sequence,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import uuid_pk

# Snapshot versions, increasing across every environment.
SNAPSHOT_VERSION_SEQ = Sequence("snapshot_version_seq", metadata=Base.metadata)


class Node(Base):
    """A worker node registered on the control plane. Its status is computed from these fields."""

    __tablename__ = "node"
    __table_args__ = (UniqueConstraint("name"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drain_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Reported in the last heartbeat.
    version: Mapped[str | None] = mapped_column(String(50))
    snapshot_version: Mapped[int | None] = mapped_column(BigInteger)
    sdk_streams: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    buffered_events: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    address: Mapped[str | None] = mapped_column(String(100))
    # The last version refused by the compatibility check.
    refused_version: Mapped[str | None] = mapped_column(String(50))
    refused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeToken(Base):
    """A node's join token. Only the sha256 of the secret is stored."""

    __tablename__ = "node_token"
    __table_args__ = (UniqueConstraint("secret_hash"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("node.id", ondelete="CASCADE"), index=True
    )
    prefix: Mapped[str] = mapped_column(String(16))
    secret_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnvironmentSnapshot(Base):
    """The latest published snapshot of an environment."""

    __tablename__ = "environment_snapshot"

    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(BigInteger)
    # sha256 hex of the snapshot body.
    digest: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NodeEventBatch(Base):
    """An event batch a worker forwarded, kept so that a retried batch is stored once."""

    __tablename__ = "node_event_batch"
    __mapper_args__ = {"eager_defaults": True}

    batch_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    node_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("node.id", ondelete="CASCADE"))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

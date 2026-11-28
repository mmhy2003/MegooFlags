"""Integrations: webhook, Slack and Teams targets that hear about a project's changes."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func, true
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import TimestampMixin, pg_enum, uuid_pk
from megooflags_api.models.enums import DeliveryStatus, IntegrationKind


class Integration(TimestampMixin, Base):
    __tablename__ = "integration"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[IntegrationKind] = mapped_column(pg_enum(IntegrationKind, "integration_kind"))
    name: Mapped[str] = mapped_column(String(200))
    # The target URL, encrypted with SecretBox: Slack and Teams URLs carry their own credential.
    url_encrypted: Mapped[str] = mapped_column(Text)
    url_hint: Mapped[str] = mapped_column(String(300))
    # The webhook signing secret, encrypted; NULL for Slack and Teams.
    secret_encrypted: Mapped[str | None] = mapped_column(Text)
    # EventGroup values.
    event_groups: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    all_environments: Mapped[bool] = mapped_column(default=True, server_default=true())
    enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
    last_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_delivery_ok: Mapped[bool | None]


class IntegrationEnvironment(Base):
    """An environment an integration is limited to when `all_environments` is false."""

    __tablename__ = "integration_environment"

    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration.id", ondelete="CASCADE"), primary_key=True
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE"), primary_key=True
    )


class IntegrationOutbox(Base):
    """An audit entry waiting to be fanned out into deliveries, written in the same transaction.

    No foreign keys, like `audit_entry`.
    """

    __tablename__ = "integration_outbox"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    audit_entry_id: Mapped[int] = mapped_column(BigInteger)
    project_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrationDelivery(Base):
    """One event sent, or to be sent, to one integration."""

    __tablename__ = "integration_delivery"
    __table_args__ = (Index("ix_integration_delivery_due", "status", "next_attempt_at"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integration.id", ondelete="CASCADE"), index=True
    )
    # NULL for a ping.
    audit_entry_id: Mapped[int | None] = mapped_column(BigInteger)
    event_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(100))
    # The event; the request body is rendered for the integration's kind when it is sent.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[DeliveryStatus] = mapped_column(
        pg_enum(DeliveryStatus, "integration_delivery_status"),
        default=DeliveryStatus.PENDING,
        server_default=DeliveryStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status_code: Mapped[int | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    # The first 2048 characters of the last response.
    response_body: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

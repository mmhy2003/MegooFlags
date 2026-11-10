import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import pg_enum, uuid_pk
from megooflags_api.models.enums import SdkKeyKind


class SdkKey(Base):
    __tablename__ = "sdk_key"
    __table_args__ = (UniqueConstraint("secret_hash"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[SdkKeyKind] = mapped_column(pg_enum(SdkKeyKind, "sdk_key_kind"))
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(14))
    secret_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExposureEvent(Base):
    __tablename__ = "exposure_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE"), index=True
    )
    flag_key: Mapped[str] = mapped_column(Text)
    variant: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class EvaluationCount(Base):
    """Evaluations per environment, flag, variant and hour. `variant` is "" for none."""

    __tablename__ = "evaluation_count"

    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE"), primary_key=True
    )
    flag_key: Mapped[str] = mapped_column(Text, primary_key=True)
    variant: Mapped[str] = mapped_column(Text, primary_key=True)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger)

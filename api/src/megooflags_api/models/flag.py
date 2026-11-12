import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import TimestampMixin, pg_enum, uuid_pk
from megooflags_api.models.enums import FlagLifecycle, FlagType


class Flag(TimestampMixin, Base):
    __tablename__ = "flag"
    __table_args__ = (
        UniqueConstraint("project_id", "key"),
        Index("ix_flag_project_archived_key", "project_id", "archived_at", "key"),
        Index("ix_flag_tags", "tags", postgresql_using="gin"),
        Index("ix_flag_project_owner", "project_id", "owner"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    type: Mapped[FlagType] = mapped_column(pg_enum(FlagType, "flag_type"))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    owner: Mapped[str | None] = mapped_column(String(200))
    # The variation table references flag, so this FK is added after both tables exist.
    off_variation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("variation.id", use_alter=True)
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The stored state; an archived flag keeps its last one and reads as "archived".
    lifecycle: Mapped[FlagLifecycle] = mapped_column(
        pg_enum(FlagLifecycle, "flag_lifecycle"),
        default=FlagLifecycle.NEW,
        server_default=FlagLifecycle.NEW.value,
    )
    # The latest evaluation-count hour in any environment, set by the lifecycle sweep.
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Variation(Base):
    __tablename__ = "variation"
    __table_args__ = (UniqueConstraint("flag_id", "key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    flag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flag.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # JSON null is a valid variation value, so store it as JSON, never as SQL NULL.
    value: Mapped[Any] = mapped_column(JSONB(none_as_null=False), nullable=False)
    sort_order: Mapped[int]

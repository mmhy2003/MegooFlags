import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import TimestampMixin, uuid_pk


class Project(TimestampMixin, Base):
    __tablename__ = "project"
    __table_args__ = (
        UniqueConstraint("key"),
        CheckConstraint("stale_after_days BETWEEN 1 AND 365", name="stale_after_days_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # A flag no environment has evaluated for this many days is stale.
    stale_after_days: Mapped[int] = mapped_column(default=30, server_default="30")


class Environment(TimestampMixin, Base):
    __tablename__ = "environment"
    __table_args__ = (
        UniqueConstraint("project_id", "key"),
        CheckConstraint("approvals_required BETWEEN 0 AND 10", name="approvals_required_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")
    # Approvals a change request needs here; 0 means writes are direct (not protected).
    approvals_required: Mapped[int] = mapped_column(default=0, server_default="0")

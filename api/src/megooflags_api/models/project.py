import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import TimestampMixin, uuid_pk


class Project(TimestampMixin, Base):
    __tablename__ = "project"
    __table_args__ = (UniqueConstraint("key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")


class Environment(TimestampMixin, Base):
    __tablename__ = "environment"
    __table_args__ = (UniqueConstraint("project_id", "key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    sort_order: Mapped[int] = mapped_column(default=0, server_default="0")

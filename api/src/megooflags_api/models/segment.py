import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import TimestampMixin, pg_enum, uuid_pk
from megooflags_api.models.enums import SegmentTargetKind


class Segment(TimestampMixin, Base):
    __tablename__ = "segment"
    __table_args__ = (UniqueConstraint("project_id", "key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")


class SegmentTarget(Base):
    __tablename__ = "segment_target"
    __table_args__ = (UniqueConstraint("segment_id", "context_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segment.id", ondelete="CASCADE"))
    context_key: Mapped[str] = mapped_column(String(256))
    kind: Mapped[SegmentTargetKind] = mapped_column(
        pg_enum(SegmentTargetKind, "segment_target_kind")
    )


class SegmentRule(Base):
    __tablename__ = "segment_rule"
    __table_args__ = (UniqueConstraint("segment_id", "position"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segment.id", ondelete="CASCADE"))
    position: Mapped[int]
    description: Mapped[str] = mapped_column(Text, default="", server_default="")

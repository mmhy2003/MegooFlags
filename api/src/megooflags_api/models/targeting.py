"""Per-environment targeting of a flag, fully normalized.

Parent FKs cascade. Cross-references (variation, segment, prerequisite flag) have no ondelete,
which is Postgres NO ACTION: the delete of something still in use is refused, but a cascading
delete of the whole flag, environment or project succeeds because it is checked at statement end.
"""

import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import TimestampMixin, pg_enum, uuid_pk
from megooflags_api.models.enums import Operator


class FlagEnvironment(TimestampMixin, Base):
    __tablename__ = "flag_environment"
    __table_args__ = (
        UniqueConstraint("flag_id", "environment_id"),
        Index(None, "environment_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    flag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flag.id", ondelete="CASCADE"))
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE")
    )
    enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    fallthrough_variation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("variation.id"))
    fallthrough_bucket_by: Mapped[str | None] = mapped_column(String(200))


class FlagTarget(Base):
    __tablename__ = "flag_target"
    __table_args__ = (UniqueConstraint("flag_environment_id", "context_key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    flag_environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flag_environment.id", ondelete="CASCADE")
    )
    variation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variation.id"))
    context_key: Mapped[str] = mapped_column(String(256))


class FlagRule(Base):
    __tablename__ = "flag_rule"
    __table_args__ = (UniqueConstraint("flag_environment_id", "position"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    flag_environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flag_environment.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    variation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("variation.id"))
    bucket_by: Mapped[str | None] = mapped_column(String(200))


class RolloutWeight(Base):
    """One variation's share of a rollout, owned by a rule or by the fallthrough."""

    __tablename__ = "rollout_weight"
    __table_args__ = (
        CheckConstraint("num_nonnulls(flag_rule_id, flag_environment_id) = 1", name="one_owner"),
        CheckConstraint("weight BETWEEN 0 AND 100000", name="weight_range"),
        UniqueConstraint("flag_rule_id", "variation_id"),
        UniqueConstraint("flag_environment_id", "variation_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    flag_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("flag_rule.id", ondelete="CASCADE")
    )
    flag_environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("flag_environment.id", ondelete="CASCADE")
    )
    variation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variation.id"))
    weight: Mapped[int]


class Clause(Base):
    """A condition of a flag rule or a segment rule."""

    __tablename__ = "clause"
    __table_args__ = (
        CheckConstraint("num_nonnulls(flag_rule_id, segment_rule_id) = 1", name="one_owner"),
        UniqueConstraint("flag_rule_id", "position"),
        UniqueConstraint("segment_rule_id", "position"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    flag_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("flag_rule.id", ondelete="CASCADE")
    )
    segment_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("segment_rule.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    attribute: Mapped[str] = mapped_column(String(200))
    operator: Mapped[Operator] = mapped_column(pg_enum(Operator, "clause_operator"))
    # Literal values; in_segment clauses keep [] here and reference segments via clause_segment.
    values: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    negate: Mapped[bool] = mapped_column(default=False, server_default=text("false"))


class ClauseSegment(Base):
    __tablename__ = "clause_segment"
    __table_args__ = (Index(None, "segment_id"),)

    clause_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clause.id", ondelete="CASCADE"), primary_key=True
    )
    segment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("segment.id"), primary_key=True)


class FlagPrerequisite(Base):
    __tablename__ = "flag_prerequisite"
    __table_args__ = (
        UniqueConstraint("flag_environment_id", "prerequisite_flag_id"),
        Index(None, "prerequisite_flag_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    flag_environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flag_environment.id", ondelete="CASCADE")
    )
    prerequisite_flag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flag.id"))
    variation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("variation.id"))
    position: Mapped[int]

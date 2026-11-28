"""Release workflows: scheduled changes, progressive rollouts and change requests."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import TimestampMixin, pg_enum, uuid_pk
from megooflags_api.models.enums import (
    ChangeRequestKind,
    ChangeRequestStatus,
    ReviewDecision,
    RolloutStatus,
    ScheduledAction,
    ScheduleStatus,
)


class ChangeRequest(TimestampMixin, Base):
    """A change to a protected environment that waits for approvals before it is applied."""

    __tablename__ = "change_request"
    __table_args__ = (
        Index("ix_change_request_project_status_created", "project_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE")
    )
    flag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flag.id", ondelete="CASCADE"))
    kind: Mapped[ChangeRequestKind] = mapped_column(
        pg_enum(ChangeRequestKind, "change_request_kind")
    )
    # The request body of the change (FlagConfigIn, ToggleIn, ScheduleIn or ProgressiveRolloutIn).
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # The flag environment's version when the request was created.
    base_version: Mapped[int]
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[ChangeRequestStatus] = mapped_column(
        pg_enum(ChangeRequestStatus, "change_request_status"),
        default=ChangeRequestStatus.OPEN,
        server_default=ChangeRequestStatus.OPEN.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    # Principal.actor_id; no foreign key, so the request outlives its author.
    author_id: Mapped[uuid.UUID | None]
    author_label: Mapped[str] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChangeRequestReview(Base):
    """One reviewer's approval or rejection of a change request."""

    __tablename__ = "change_request_review"
    __table_args__ = (UniqueConstraint("change_request_id", "reviewer_id"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_request.id", ondelete="CASCADE")
    )
    reviewer_id: Mapped[uuid.UUID]
    reviewer_label: Mapped[str] = mapped_column(Text)
    decision: Mapped[ReviewDecision] = mapped_column(pg_enum(ReviewDecision, "review_decision"))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChangeRequestComment(Base):
    __tablename__ = "change_request_comment"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("change_request.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID | None]
    author_label: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduledChange(Base):
    """A flag change in one environment that the scheduler applies at `run_at`."""

    __tablename__ = "scheduled_change"
    __table_args__ = (Index("ix_scheduled_change_status_run_at", "status", "run_at"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    flag_environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flag_environment.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[ScheduledAction] = mapped_column(pg_enum(ScheduledAction, "scheduled_action"))
    # The fallthrough rollout weights of a set_rollout ([{variation, weight}]); NULL otherwise.
    weights: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ScheduleStatus] = mapped_column(
        pg_enum(ScheduleStatus, "schedule_status"),
        default=ScheduleStatus.PENDING,
        server_default=ScheduleStatus.PENDING.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None]
    created_by_label: Mapped[str] = mapped_column(Text)
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("change_request.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProgressiveRollout(TimestampMixin, Base):
    """Moves fallthrough traffic from one variation to another in timed steps."""

    __tablename__ = "progressive_rollout"
    __table_args__ = (
        Index("ix_progressive_rollout_status_next_step_at", "status", "next_step_at"),
        # At most one live (active or paused) rollout per flag environment.
        Index(
            "uq_progressive_rollout_live",
            "flag_environment_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'paused')"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    flag_environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("flag_environment.id", ondelete="CASCADE"), index=True
    )
    # NULL once the variation is deleted, which fails the rollout.
    from_variation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("variation.id", ondelete="SET NULL")
    )
    to_variation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("variation.id", ondelete="SET NULL")
    )
    # [{percentage, wait_minutes}]; percentage is the "to" share in 0-100000 units.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    current_step: Mapped[int] = mapped_column(default=0, server_default="0")
    next_step_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The wait left on the current step while the rollout is paused.
    remaining_seconds: Mapped[int | None]
    status: Mapped[RolloutStatus] = mapped_column(
        pg_enum(RolloutStatus, "rollout_status"),
        default=RolloutStatus.ACTIVE,
        server_default=RolloutStatus.ACTIVE.value,
    )
    last_applied_weights: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None]
    created_by_label: Mapped[str] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

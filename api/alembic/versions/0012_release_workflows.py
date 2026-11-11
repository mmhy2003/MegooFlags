"""Release workflows: change requests, scheduled changes, progressive rollouts, flag lifecycle.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-11 22:40:28.153985

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

FLAG_LIFECYCLE = postgresql.ENUM(
    "new", "active", "permanent", "stale", name="flag_lifecycle", create_type=False
)

ENUM_TYPES = (
    "flag_lifecycle",
    "scheduled_action",
    "schedule_status",
    "rollout_status",
    "change_request_kind",
    "change_request_status",
    "review_decision",
)

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "change_request",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("flag_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("config", "toggle", "schedule", "rollout", name="change_request_kind"),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open", "applied", "rejected", "cancelled", "failed", name="change_request_status"
            ),
            server_default="open",
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("author_label", sa.Text(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environment.id"],
            name=op.f("fk_change_request_environment_id_environment"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["flag_id"],
            ["flag.id"],
            name=op.f("fk_change_request_flag_id_flag"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_change_request_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_request")),
    )
    op.create_index(
        "ix_change_request_project_status_created",
        "change_request",
        ["project_id", "status", "created_at"],
        unique=False,
    )
    op.create_table(
        "change_request_comment",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("change_request_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("author_label", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["change_request_id"],
            ["change_request.id"],
            name=op.f("fk_change_request_comment_change_request_id_change_request"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_request_comment")),
    )
    op.create_index(
        op.f("ix_change_request_comment_change_request_id"),
        "change_request_comment",
        ["change_request_id"],
        unique=False,
    )
    op.create_table(
        "change_request_review",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("change_request_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_label", sa.Text(), nullable=False),
        sa.Column("decision", sa.Enum("approve", "reject", name="review_decision"), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["change_request_id"],
            ["change_request.id"],
            name=op.f("fk_change_request_review_change_request_id_change_request"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_request_review")),
        sa.UniqueConstraint(
            "change_request_id",
            "reviewer_id",
            name=op.f("uq_change_request_review_change_request_id_reviewer_id"),
        ),
    )
    op.create_table(
        "progressive_rollout",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("flag_environment_id", sa.Uuid(), nullable=False),
        sa.Column("from_variation_id", sa.Uuid(), nullable=True),
        sa.Column("to_variation_id", sa.Uuid(), nullable=True),
        sa.Column("steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_step", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_step_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remaining_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "paused",
                "completed",
                "aborted",
                "failed",
                "cancelled",
                name="rollout_status",
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column("last_applied_weights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_label", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["flag_environment_id"],
            ["flag_environment.id"],
            name=op.f("fk_progressive_rollout_flag_environment_id_flag_environment"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_variation_id"],
            ["variation.id"],
            name=op.f("fk_progressive_rollout_from_variation_id_variation"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["to_variation_id"],
            ["variation.id"],
            name=op.f("fk_progressive_rollout_to_variation_id_variation"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_progressive_rollout")),
    )
    op.create_index(
        op.f("ix_progressive_rollout_flag_environment_id"),
        "progressive_rollout",
        ["flag_environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_progressive_rollout_status_next_step_at",
        "progressive_rollout",
        ["status", "next_step_at"],
        unique=False,
    )
    op.create_index(
        "uq_progressive_rollout_live",
        "progressive_rollout",
        ["flag_environment_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'paused')"),
    )
    op.create_table(
        "scheduled_change",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("flag_environment_id", sa.Uuid(), nullable=False),
        sa.Column(
            "action",
            sa.Enum("enable", "disable", "set_rollout", name="scheduled_action"),
            nullable=False,
        ),
        sa.Column("weights", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "done", "failed", "cancelled", name="schedule_status"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_label", sa.Text(), nullable=False),
        sa.Column("change_request_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["change_request_id"],
            ["change_request.id"],
            name=op.f("fk_scheduled_change_change_request_id_change_request"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["flag_environment_id"],
            ["flag_environment.id"],
            name=op.f("fk_scheduled_change_flag_environment_id_flag_environment"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_change")),
    )
    op.create_index(
        op.f("ix_scheduled_change_flag_environment_id"),
        "scheduled_change",
        ["flag_environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_scheduled_change_status_run_at", "scheduled_change", ["status", "run_at"], unique=False
    )
    op.add_column(
        "environment",
        sa.Column("approvals_required", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_environment_approvals_required_range"),
        "environment",
        "approvals_required BETWEEN 0 AND 10",
    )
    # add_column does not create the enum type, so create it first.
    FLAG_LIFECYCLE.create(op.get_bind())
    op.add_column(
        "flag", sa.Column("lifecycle", FLAG_LIFECYCLE, server_default="new", nullable=False)
    )
    op.add_column("flag", sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "project", sa.Column("stale_after_days", sa.Integer(), server_default="30", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_project_stale_after_days_range"),
        "project",
        "stale_after_days BETWEEN 1 AND 365",
    )
    op.execute(
        "UPDATE role SET permissions = array_append(permissions, 'change.approve')"
        " WHERE builtin AND key IN ('admin', 'editor')"
        " AND NOT 'change.approve' = ANY(permissions)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE role SET permissions = array_remove(permissions, 'change.approve')")
    op.execute(
        "UPDATE api_token SET permissions = array_remove(permissions, 'change.approve')"
        " WHERE permissions IS NOT NULL"
    )
    op.drop_constraint(op.f("ck_project_stale_after_days_range"), "project", type_="check")
    op.drop_column("project", "stale_after_days")
    op.drop_column("flag", "last_evaluated_at")
    op.drop_column("flag", "lifecycle")
    op.drop_constraint(
        op.f("ck_environment_approvals_required_range"), "environment", type_="check"
    )
    op.drop_column("environment", "approvals_required")
    op.drop_index("ix_scheduled_change_status_run_at", table_name="scheduled_change")
    op.drop_index(op.f("ix_scheduled_change_flag_environment_id"), table_name="scheduled_change")
    op.drop_table("scheduled_change")
    op.drop_index(
        "uq_progressive_rollout_live",
        table_name="progressive_rollout",
        postgresql_where=sa.text("status IN ('active', 'paused')"),
    )
    op.drop_index("ix_progressive_rollout_status_next_step_at", table_name="progressive_rollout")
    op.drop_index(
        op.f("ix_progressive_rollout_flag_environment_id"), table_name="progressive_rollout"
    )
    op.drop_table("progressive_rollout")
    op.drop_table("change_request_review")
    op.drop_index(
        op.f("ix_change_request_comment_change_request_id"), table_name="change_request_comment"
    )
    op.drop_table("change_request_comment")
    op.drop_index("ix_change_request_project_status_created", table_name="change_request")
    op.drop_table("change_request")
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE {name}")

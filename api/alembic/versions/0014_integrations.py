"""Integrations: webhook, Slack and Teams targets, the outbox and deliveries.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

ENUM_TYPES = ("integration_kind", "integration_delivery_status")

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "integration",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind", sa.Enum("webhook", "slack", "teams", name="integration_kind"), nullable=False
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("url_encrypted", sa.Text(), nullable=False),
        sa.Column("url_hint", sa.String(length=300), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("event_groups", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("all_environments", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_delivery_ok", sa.Boolean(), nullable=True),
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
            ["project_id"],
            ["project.id"],
            name=op.f("fk_integration_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration")),
    )
    op.create_index(op.f("ix_integration_project_id"), "integration", ["project_id"], unique=False)
    op.create_table(
        "integration_environment",
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environment.id"],
            name=op.f("fk_integration_environment_environment_id_environment"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["integration.id"],
            name=op.f("fk_integration_environment_integration_id_integration"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "integration_id", "environment_id", name=op.f("pk_integration_environment")
        ),
    )
    op.create_table(
        "integration_outbox",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("audit_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_outbox")),
    )
    op.create_table(
        "integration_delivery",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("integration_id", sa.Uuid(), nullable=False),
        sa.Column("audit_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "delivered", "failed", name="integration_delivery_status"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["integration_id"],
            ["integration.id"],
            name=op.f("fk_integration_delivery_integration_id_integration"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_delivery")),
    )
    op.create_index(
        op.f("ix_integration_delivery_created_at"),
        "integration_delivery",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integration_delivery_integration_id"),
        "integration_delivery",
        ["integration_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_delivery_due",
        "integration_delivery",
        ["status", "next_attempt_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_integration_delivery_due", table_name="integration_delivery")
    op.drop_index(op.f("ix_integration_delivery_integration_id"), table_name="integration_delivery")
    op.drop_index(op.f("ix_integration_delivery_created_at"), table_name="integration_delivery")
    op.drop_table("integration_delivery")
    op.drop_table("integration_outbox")
    op.drop_table("integration_environment")
    op.drop_index(op.f("ix_integration_project_id"), table_name="integration")
    op.drop_table("integration")
    for name in ENUM_TYPES:
        op.execute(f"DROP TYPE {name}")

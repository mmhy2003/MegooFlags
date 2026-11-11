"""Append-only audit log.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10 21:16:17.027562

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_entry",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_kind", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_label", sa.Text(), nullable=False),
        sa.Column("auth_method", sa.Text(), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=False),
        sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_entry")),
    )
    op.create_index(op.f("ix_audit_entry_action"), "audit_entry", ["action"], unique=False)
    op.create_index(op.f("ix_audit_entry_actor_id"), "audit_entry", ["actor_id"], unique=False)
    op.create_index(
        op.f("ix_audit_entry_occurred_at"), "audit_entry", ["occurred_at"], unique=False
    )
    op.create_index(op.f("ix_audit_entry_project_id"), "audit_entry", ["project_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_audit_entry_project_id"), table_name="audit_entry")
    op.drop_index(op.f("ix_audit_entry_occurred_at"), table_name="audit_entry")
    op.drop_index(op.f("ix_audit_entry_actor_id"), table_name="audit_entry")
    op.drop_index(op.f("ix_audit_entry_action"), table_name="audit_entry")
    op.drop_table("audit_entry")

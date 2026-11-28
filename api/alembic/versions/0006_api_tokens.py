"""Hashed API tokens (service and personal).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10 20:47:41.681699

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "api_token",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.Enum("service", "personal", name="api_token_kind"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("prefix", sa.String(length=14), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("permissions", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_from_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(kind = 'personal') = (owner_user_id IS NOT NULL)",
            name=op.f("ck_api_token_personal_has_owner"),
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environment.id"],
            name=op.f("fk_api_token_environment_id_environment"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["user.id"],
            name=op.f("fk_api_token_owner_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_api_token_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from_id"],
            ["api_token.id"],
            name=op.f("fk_api_token_rotated_from_id_api_token"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_token")),
        sa.UniqueConstraint("secret_hash", name=op.f("uq_api_token_secret_hash")),
    )
    op.create_index(
        op.f("ix_api_token_owner_user_id"), "api_token", ["owner_user_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_api_token_owner_user_id"), table_name="api_token")
    op.drop_table("api_token")
    op.execute("DROP TYPE api_token_kind")

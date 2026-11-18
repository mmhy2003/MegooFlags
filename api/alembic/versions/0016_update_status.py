"""Update status: the latest MegooFlags release seen by the release check.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    update_status = op.create_table(
        "update_status",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("latest_version", sa.String(length=50), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_update_status_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_update_status")),
    )
    op.bulk_insert(update_status, [{"id": 1}])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("update_status")

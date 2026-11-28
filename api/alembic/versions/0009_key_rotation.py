"""SDK key rotation: expiry and the key a key was rotated from.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-11 00:29:36.041784

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("sdk_key", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sdk_key", sa.Column("rotated_from_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_sdk_key_rotated_from_id_sdk_key"),
        "sdk_key",
        "sdk_key",
        ["rotated_from_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("fk_sdk_key_rotated_from_id_sdk_key"), "sdk_key", type_="foreignkey")
    op.drop_column("sdk_key", "rotated_from_id")
    op.drop_column("sdk_key", "expires_at")

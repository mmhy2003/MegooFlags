"""User UI preferences: theme and sidebar state.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-12 16:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

USER_THEME = postgresql.ENUM("light", "dark", "system", name="user_theme", create_type=False)

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # add_column does not create the enum type, so create it first.
    USER_THEME.create(op.get_bind())
    op.add_column("user", sa.Column("theme", USER_THEME, server_default="system", nullable=False))
    op.add_column(
        "user",
        sa.Column("sidebar_collapsed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("user", "sidebar_collapsed")
    op.drop_column("user", "theme")
    op.execute("DROP TYPE user_theme")

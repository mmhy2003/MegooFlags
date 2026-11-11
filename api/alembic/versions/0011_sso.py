"""SSO: identity providers, IdP group mappings and linked identities.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-11 11:00:06.611163

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sso_provider",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("protocol", sa.Enum("oidc", "saml", name="sso_protocol"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("jit_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("link_by_verified_email", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("default_role_id", sa.Uuid(), nullable=True),
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
            ["default_role_id"],
            ["role.id"],
            name=op.f("fk_sso_provider_default_role_id_role"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sso_provider")),
        sa.UniqueConstraint("key", name=op.f("uq_sso_provider_key")),
    )
    op.create_table(
        "sso_group_mapping",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("idp_group", sa.String(length=500), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("role_id", sa.Uuid(), nullable=True),
        sa.Column(
            "scope",
            postgresql.ENUM("org", "project", "environment", name="scope_kind", create_type=False),
            nullable=True,
        ),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(team_id IS NOT NULL AND role_id IS NULL AND scope IS NULL"
            " AND project_id IS NULL AND environment_id IS NULL)"
            " OR (team_id IS NULL AND role_id IS NOT NULL AND ("
            "(scope = 'org' AND project_id IS NULL AND environment_id IS NULL)"
            " OR (scope = 'project' AND project_id IS NOT NULL AND environment_id IS NULL)"
            " OR (scope = 'environment' AND project_id IS NOT NULL"
            " AND environment_id IS NOT NULL)))",
            name=op.f("ck_sso_group_mapping_one_target"),
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environment.id"],
            name=op.f("fk_sso_group_mapping_environment_id_environment"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_sso_group_mapping_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["sso_provider.id"],
            name=op.f("fk_sso_group_mapping_provider_id_sso_provider"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["role.id"],
            name=op.f("fk_sso_group_mapping_role_id_role"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name=op.f("fk_sso_group_mapping_team_id_team"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sso_group_mapping")),
    )
    op.create_index(
        op.f("ix_sso_group_mapping_provider_id"), "sso_group_mapping", ["provider_id"], unique=False
    )
    op.create_table(
        "user_identity",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["sso_provider.id"],
            name=op.f("fk_user_identity_provider_id_sso_provider"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_user_identity_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_identity")),
        sa.UniqueConstraint(
            "provider_id", "subject", name=op.f("uq_user_identity_provider_id_subject")
        ),
    )
    op.create_index(op.f("ix_user_identity_user_id"), "user_identity", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_user_identity_user_id"), table_name="user_identity")
    op.drop_table("user_identity")
    op.drop_index(op.f("ix_sso_group_mapping_provider_id"), table_name="sso_group_mapping")
    op.drop_table("sso_group_mapping")
    op.drop_table("sso_provider")
    op.execute("DROP TYPE sso_protocol")

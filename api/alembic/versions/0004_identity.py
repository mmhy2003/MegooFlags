"""Users, teams, roles, role bindings and org settings; seeds the built-in roles.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-10 20:21:24.583964

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Literal copies of auth.rbac.BUILTIN_ROLES, so this migration never changes with the code.
ADMIN = [
    "audit.read",
    "environment.manage",
    "evaluate",
    "flag.archive",
    "flag.config.write",
    "flag.create",
    "flag.toggle",
    "flag.update",
    "member.manage",
    "org.manage",
    "project.create",
    "project.manage",
    "project.read",
    "role.manage",
    "sdk_key.manage",
    "sdk_key.read",
    "segment.write",
    "token.manage",
]
EDITOR = [
    "environment.manage",
    "evaluate",
    "flag.archive",
    "flag.config.write",
    "flag.create",
    "flag.toggle",
    "flag.update",
    "project.create",
    "project.manage",
    "project.read",
    "sdk_key.manage",
    "sdk_key.read",
    "segment.write",
]
VIEWER = ["audit.read", "evaluate", "project.read", "sdk_key.read"]


def upgrade() -> None:
    """Upgrade schema."""
    org_settings = op.create_table(
        "org_settings",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("require_mfa", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_org_settings_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_settings")),
    )
    role = op.create_table(
        "role",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("builtin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("permissions", postgresql.ARRAY(sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role")),
        sa.UniqueConstraint("key", name=op.f("uq_role_key")),
    )
    op.create_table(
        "team",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team")),
        sa.UniqueConstraint("key", name=op.f("uq_team_key")),
    )
    op.create_table(
        "user",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "disabled", name="user_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user")),
    )
    op.create_index("uq_user_email_lower", "user", [sa.literal_column("lower(email)")], unique=True)
    op.create_table(
        "team_member",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.Text(), server_default="manual", nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"], ["team.id"], name=op.f("fk_team_member_team_id_team"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_team_member_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("team_id", "user_id", name=op.f("pk_team_member")),
    )
    op.create_index(op.f("ix_team_member_user_id"), "team_member", ["user_id"], unique=False)
    op.create_table(
        "role_binding",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "subject_kind",
            sa.Enum("user", "team", "service_token", name="subject_kind"),
            nullable=False,
        ),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column(
            "scope", sa.Enum("org", "project", "environment", name="scope_kind"), nullable=False
        ),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.Text(), server_default="manual", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(scope = 'org' AND project_id IS NULL AND environment_id IS NULL)"
            " OR (scope = 'project' AND project_id IS NOT NULL AND environment_id IS NULL)"
            " OR (scope = 'environment' AND project_id IS NOT NULL"
            " AND environment_id IS NOT NULL)",
            name=op.f("ck_role_binding_scope_matches"),
        ),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environment.id"],
            name=op.f("fk_role_binding_environment_id_environment"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_role_binding_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["role.id"], name=op.f("fk_role_binding_role_id_role"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_binding")),
        sa.UniqueConstraint(
            "subject_kind",
            "subject_id",
            "role_id",
            "scope",
            "project_id",
            "environment_id",
            name="uq_role_binding_subject_role_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_role_binding_subject_kind_subject_id"),
        "role_binding",
        ["subject_kind", "subject_id"],
        unique=False,
    )
    op.bulk_insert(
        role,
        [
            {
                "key": "admin",
                "name": "Admin",
                "description": "Full access, including members, roles, tokens and the audit log.",
                "builtin": True,
                "permissions": ADMIN,
            },
            {
                "key": "editor",
                "name": "Editor",
                "description": "Manages projects, environments, flags, segments and SDK keys.",
                "builtin": True,
                "permissions": EDITOR,
            },
            {
                "key": "viewer",
                "name": "Viewer",
                "description": "Read-only access, including the audit log.",
                "builtin": True,
                "permissions": VIEWER,
            },
        ],
    )
    op.bulk_insert(org_settings, [{"id": 1, "require_mfa": False}])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_role_binding_subject_kind_subject_id"), table_name="role_binding")
    op.drop_table("role_binding")
    op.drop_index(op.f("ix_team_member_user_id"), table_name="team_member")
    op.drop_table("team_member")
    op.drop_index("uq_user_email_lower", table_name="user")
    op.drop_table("user")
    op.drop_table("team")
    op.drop_table("role")
    op.drop_table("org_settings")
    op.execute("DROP TYPE scope_kind")
    op.execute("DROP TYPE subject_kind")
    op.execute("DROP TYPE user_status")

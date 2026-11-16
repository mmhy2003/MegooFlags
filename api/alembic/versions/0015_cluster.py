"""Cluster: worker nodes, node tokens, environment snapshots and event batches.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(sa.schema.CreateSequence(sa.Sequence("snapshot_version_seq")))
    op.create_table(
        "node",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drain_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.String(length=50), nullable=True),
        sa.Column("snapshot_version", sa.BigInteger(), nullable=True),
        sa.Column("sdk_streams", sa.Integer(), server_default="0", nullable=False),
        sa.Column("buffered_events", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("address", sa.String(length=100), nullable=True),
        sa.Column("refused_version", sa.String(length=50), nullable=True),
        sa.Column("refused_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_node")),
        sa.UniqueConstraint("name", name=op.f("uq_node_name")),
    )
    op.create_table(
        "node_token",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["node.id"],
            name=op.f("fk_node_token_node_id_node"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_node_token")),
        sa.UniqueConstraint("secret_hash", name=op.f("uq_node_token_secret_hash")),
    )
    op.create_index(op.f("ix_node_token_node_id"), "node_token", ["node_id"], unique=False)
    op.create_table(
        "environment_snapshot",
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["environment.id"],
            name=op.f("fk_environment_snapshot_environment_id_environment"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("environment_id", name=op.f("pk_environment_snapshot")),
    )
    op.create_table(
        "node_event_batch",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["node.id"],
            name=op.f("fk_node_event_batch_node_id_node"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("batch_id", name=op.f("pk_node_event_batch")),
    )
    op.create_index(
        op.f("ix_node_event_batch_received_at"), "node_event_batch", ["received_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_node_event_batch_received_at"), table_name="node_event_batch")
    op.drop_table("node_event_batch")
    op.drop_table("environment_snapshot")
    op.drop_index(op.f("ix_node_token_node_id"), table_name="node_token")
    op.drop_table("node_token")
    op.drop_table("node")
    op.execute(sa.schema.DropSequence(sa.Sequence("snapshot_version_seq")))

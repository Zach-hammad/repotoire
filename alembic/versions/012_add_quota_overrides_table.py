"""Add quota_overrides table for persisted quota overrides with audit trail.

Revision ID: 012_quota_overrides
Revises: 011_add_fixes_tables
Create Date: 2024-12-04 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "012_quota_overrides"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the quota_override_type enum
    quota_override_type = postgresql.ENUM(
        "sandbox_minutes",
        "concurrent_sessions",
        "storage_gb",
        "analysis_per_month",
        "max_repo_size_mb",
        "daily_sandbox_minutes",
        "monthly_sandbox_minutes",
        "sandboxes_per_day",
        name="quota_override_type",
        create_type=False,
    )
    quota_override_type.create(op.get_bind(), checkfirst=True)

    # Create quota_overrides table
    op.create_table(
        "quota_overrides",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("override_type", quota_override_type, nullable=False),
        sa.Column(
            "original_limit",
            sa.Integer(),
            nullable=False,
            comment="Original tier limit at time of override creation",
        ),
        sa.Column(
            "override_limit",
            sa.Integer(),
            nullable=False,
            comment="New limit granted by this override",
        ),
        sa.Column(
            "reason",
            sa.Text(),
            nullable=False,
            comment="Why this override was granted",
        ),
        sa.Column(
            "created_by_id",
            sa.UUID(),
            nullable=False,
            comment="Admin who created this override",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When this override expires (null = never)",
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When this override was revoked",
        ),
        sa.Column(
            "revoked_by_id",
            sa.UUID(),
            nullable=True,
            comment="Admin who revoked this override",
        ),
        sa.Column(
            "revoke_reason",
            sa.Text(),
            nullable=True,
            comment="Why this override was revoked",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes
    op.create_index(
        "ix_quota_overrides_org_type",
        "quota_overrides",
        ["organization_id", "override_type"],
        unique=False,
    )
    op.create_index(
        "ix_quota_overrides_created_by",
        "quota_overrides",
        ["created_by_id"],
        unique=False,
    )
    op.create_index(
        "ix_quota_overrides_expires_at",
        "quota_overrides",
        ["expires_at"],
        unique=False,
    )

    # Create partial index for active overrides (PostgreSQL only)
    op.execute(
        """
        CREATE INDEX ix_quota_overrides_active
        ON quota_overrides (organization_id, override_type)
        WHERE revoked_at IS NULL
        """
    )


def downgrade() -> None:
    # Drop indexes
    op.execute("DROP INDEX IF EXISTS ix_quota_overrides_active")
    op.drop_index("ix_quota_overrides_expires_at", table_name="quota_overrides")
    op.drop_index("ix_quota_overrides_created_by", table_name="quota_overrides")
    op.drop_index("ix_quota_overrides_org_type", table_name="quota_overrides")

    # Drop table
    op.drop_table("quota_overrides")

    # Drop enum
    sa.Enum(name="quota_override_type").drop(op.get_bind(), checkfirst=True)

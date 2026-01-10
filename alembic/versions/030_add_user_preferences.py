"""Add user_preferences table.

Revision ID: 030
Revises: 029
Create Date: 2026-01-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user_preferences table for dashboard settings."""
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        # Appearance settings
        sa.Column("theme", sa.String(20), nullable=False, server_default="system"),
        # Notification settings
        sa.Column("new_fix_alerts", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("critical_security_alerts", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("weekly_summary", sa.Boolean(), nullable=False, server_default="false"),
        # Auto-fix settings
        sa.Column("auto_approve_high_confidence", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("generate_tests", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("create_git_branches", sa.Boolean(), nullable=False, server_default="true"),
        # Timestamps
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
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_user_preferences_user_id",
        "user_preferences",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop user_preferences table."""
    op.drop_index("ix_user_preferences_user_id", table_name="user_preferences")
    op.drop_table("user_preferences")

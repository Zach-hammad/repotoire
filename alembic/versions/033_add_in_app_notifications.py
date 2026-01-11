"""Add in_app_notifications table.

Revision ID: 033
Revises: 032
Create Date: 2026-01-10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create in_app_notifications table."""
    op.create_table(
        "in_app_notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(50), nullable=False, server_default="system"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_url", sa.String(2048), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=True),
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
    )
    # Create indexes for common queries
    op.create_index(
        "ix_in_app_notifications_user_id",
        "in_app_notifications",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_user_read",
        "in_app_notifications",
        ["user_id", "read"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_user_created",
        "in_app_notifications",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop in_app_notifications table."""
    op.drop_index("ix_notifications_user_created", table_name="in_app_notifications")
    op.drop_index("ix_notifications_user_read", table_name="in_app_notifications")
    op.drop_index("ix_in_app_notifications_user_id", table_name="in_app_notifications")
    op.drop_table("in_app_notifications")

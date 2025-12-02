"""Add email_preferences table.

Revision ID: 005
Revises: 004
Create Date: 2024-12-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create email_preferences table."""
    op.create_table(
        "email_preferences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("analysis_complete", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("analysis_failed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("health_regression", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("weekly_digest", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("team_notifications", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("billing_notifications", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("regression_threshold", sa.Integer(), nullable=False, server_default="10"),
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
        "ix_email_preferences_user_id",
        "email_preferences",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop email_preferences table."""
    op.drop_index("ix_email_preferences_user_id", table_name="email_preferences")
    op.drop_table("email_preferences")

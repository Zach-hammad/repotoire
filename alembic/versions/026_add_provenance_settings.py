"""Add provenance_settings table.

Revision ID: 026
Revises: 025
Create Date: 2024-12-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create provenance_settings table for user provenance display preferences."""
    op.create_table(
        "provenance_settings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        # Privacy settings (privacy-first defaults)
        sa.Column("show_author_names", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("show_author_avatars", sa.Boolean(), nullable=False, server_default="false"),
        # Display settings
        sa.Column("show_confidence_badges", sa.Boolean(), nullable=False, server_default="true"),
        # Performance settings
        sa.Column("auto_query_provenance", sa.Boolean(), nullable=False, server_default="false"),
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
        "ix_provenance_settings_user_id",
        "provenance_settings",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop provenance_settings table."""
    op.drop_index("ix_provenance_settings_user_id", table_name="provenance_settings")
    op.drop_table("provenance_settings")

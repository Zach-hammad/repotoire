"""Add missing columns to email_preferences table.

Revision ID: 038
Revises: 037_add_byok_api_keys
Create Date: 2026-02-04
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add in_app_notifications column."""
    # Add in_app_notifications column (regression_threshold already exists)
    op.add_column(
        "email_preferences",
        sa.Column(
            "in_app_notifications",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    """Remove the added column."""
    op.drop_column("email_preferences", "in_app_notifications")

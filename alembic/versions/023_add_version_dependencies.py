"""Add dependencies column to marketplace_asset_versions.

Revision ID: 023
Revises: 022
Create Date: 2025-12-19

Adds:
- dependencies JSONB column to marketplace_asset_versions

Dependencies are stored as a JSONB object mapping asset slugs to version constraints:
    {"@repotoire/security-scanner": "^1.0.0", "@myorg/helper": "~2.1.0"}

This enables npm-style dependency resolution for marketplace assets.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add dependencies column to marketplace_asset_versions
    # Format: {"@slug/name": "^1.0.0", ...}
    op.add_column(
        "marketplace_asset_versions",
        sa.Column(
            "dependencies",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default="{}",
            comment="Map of asset slug to version constraint",
        ),
    )


def downgrade() -> None:
    op.drop_column("marketplace_asset_versions", "dependencies")

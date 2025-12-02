"""Add clerk_org_id to organizations table.

Revision ID: 007
Revises: 006
Create Date: 2024-12-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add clerk_org_id column to organizations table."""
    op.add_column(
        "organizations",
        sa.Column("clerk_org_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_organizations_clerk_org_id",
        "organizations",
        ["clerk_org_id"],
        unique=True,
    )


def downgrade() -> None:
    """Remove clerk_org_id column from organizations table."""
    op.drop_index("ix_organizations_clerk_org_id", table_name="organizations")
    op.drop_column("organizations", "clerk_org_id")

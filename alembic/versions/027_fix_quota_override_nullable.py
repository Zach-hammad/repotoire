"""Fix QuotaOverride created_by_id nullable constraint.

The created_by_id column had nullable=False with ondelete="SET NULL" which would
cause a constraint violation when the referenced user is deleted. Changed to nullable=True.

Revision ID: 027
Revises: 026
Create Date: 2024-12-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make created_by_id nullable to support SET NULL on user deletion."""
    op.alter_column(
        "quota_overrides",
        "created_by_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    """Revert created_by_id to non-nullable."""
    # Note: This may fail if there are NULL values in the column
    op.alter_column(
        "quota_overrides",
        "created_by_id",
        existing_type=sa.UUID(),
        nullable=False,
    )

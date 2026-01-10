"""Add stale status to fix_status enum.

Revision ID: 029
Revises: 028
Create Date: 2026-01-09

Adds 'stale' status to the fix_status enum for fixes where the
target code has changed since the fix was generated.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add 'stale' value to fix_status enum."""
    # PostgreSQL allows adding values to enums with ALTER TYPE
    # Note: ADD VALUE cannot be run inside a transaction block in older PostgreSQL versions
    # but Neon (PostgreSQL 15+) supports it
    op.execute("ALTER TYPE fix_status ADD VALUE IF NOT EXISTS 'stale'")


def downgrade() -> None:
    """Remove 'stale' value from fix_status enum.

    Note: PostgreSQL doesn't support removing values from enums directly.
    To truly downgrade, you would need to:
    1. Create a new enum without 'stale'
    2. Update all 'stale' fixes to another status (e.g., 'failed')
    3. Alter the column to use the new enum
    4. Drop the old enum

    For simplicity, we just update any 'stale' fixes to 'failed'.
    """
    op.execute("UPDATE fixes SET status = 'failed' WHERE status = 'stale'")
    # Note: The enum value will remain but be unused

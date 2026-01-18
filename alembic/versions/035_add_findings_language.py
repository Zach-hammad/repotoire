"""Add language column to findings table.

This column stores the primary programming language of affected files
(e.g., python, typescript, java), enabling language-specific filtering
and reporting.

Revision ID: 035
Revises: 034
Create Date: 2026-01-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add language column to findings table."""
    op.add_column(
        "findings",
        sa.Column(
            "language",
            sa.String(length=50),
            nullable=True,
            comment="Primary language of affected files (e.g., python, typescript)",
        ),
    )
    # Add index for filtering by language
    op.create_index("ix_findings_language", "findings", ["language"], unique=False)


def downgrade() -> None:
    """Remove language column from findings table."""
    op.drop_index("ix_findings_language", table_name="findings")
    op.drop_column("findings", "language")

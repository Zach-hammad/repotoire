"""Add issues_score column to analysis_runs table.

This column stores the score based on finding severity counts,
which is now factored into the overall health score calculation.

Revision ID: 034
Revises: 033
Create Date: 2026-01-12

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add issues_score column to analysis_runs table."""
    op.add_column(
        "analysis_runs",
        sa.Column("issues_score", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Remove issues_score column from analysis_runs table."""
    op.drop_column("analysis_runs", "issues_score")

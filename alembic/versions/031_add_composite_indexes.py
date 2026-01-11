"""Add composite indexes for Fix and FixComment tables.

Revision ID: 031
Revises: 030
Create Date: 2026-01-09

These composite indexes optimize common query patterns:
- ix_fixes_analysis_run_status: Search fixes by analysis_run_id and status
- ix_fixes_analysis_run_created: Get fixes by analysis_run_id ordered by created_at DESC
- ix_fixes_finding_created: Get fixes by finding_id ordered by created_at DESC
- ix_fix_comments_fix_created: Get comments by fix_id ordered by created_at DESC
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add composite indexes for common query patterns."""
    # Composite index for search with both filters (analysis_run_id + status)
    op.create_index(
        "ix_fixes_analysis_run_status",
        "fixes",
        ["analysis_run_id", "status"],
        unique=False,
    )

    # Composite indexes with DESC ordering require raw SQL
    op.execute("""
        CREATE INDEX ix_fixes_analysis_run_created
        ON fixes (analysis_run_id, created_at DESC)
    """)

    op.execute("""
        CREATE INDEX ix_fixes_finding_created
        ON fixes (finding_id, created_at DESC)
    """)

    op.execute("""
        CREATE INDEX ix_fix_comments_fix_created
        ON fix_comments (fix_id, created_at DESC)
    """)


def downgrade() -> None:
    """Remove composite indexes."""
    # Drop fix_comments composite index
    op.drop_index("ix_fix_comments_fix_created", table_name="fix_comments")

    # Drop fixes composite indexes
    op.drop_index("ix_fixes_finding_created", table_name="fixes")
    op.drop_index("ix_fixes_analysis_run_created", table_name="fixes")
    op.drop_index("ix_fixes_analysis_run_status", table_name="fixes")

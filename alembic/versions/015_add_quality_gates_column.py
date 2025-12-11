"""Add quality_gates and pr_analysis_enabled columns to github_repositories.

Revision ID: 015
Revises: 014
Create Date: 2024-12-10

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add pr_analysis_enabled and quality_gates columns.

    - pr_analysis_enabled: Boolean to control PR analysis (default True)
    - quality_gates: JSONB column for configurable quality gates
    """
    # Add pr_analysis_enabled column
    op.add_column(
        "github_repositories",
        sa.Column(
            "pr_analysis_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="Whether to analyze pull requests",
        ),
    )

    # Add quality_gates JSONB column
    op.add_column(
        "github_repositories",
        sa.Column(
            "quality_gates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Quality gate configuration: {enabled, block_on_critical, block_on_high, min_health_score, max_new_issues}",
        ),
    )


def downgrade() -> None:
    """Remove pr_analysis_enabled and quality_gates columns."""
    op.drop_column("github_repositories", "quality_gates")
    op.drop_column("github_repositories", "pr_analysis_enabled")

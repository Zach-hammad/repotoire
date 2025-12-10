"""Add auto_analyze field to github_repositories.

Revision ID: 013
Revises: 012
Create Date: 2024-12-04

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: Union[str, None] = "012_quota_overrides"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add auto_analyze column to github_repositories table.

    Default is True so existing enabled repos will auto-analyze on push.
    Auto-analysis requires: enabled=True AND org.plan_tier in (pro, enterprise).
    """
    op.add_column(
        "github_repositories",
        sa.Column(
            "auto_analyze",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="Whether to auto-analyze on push events (requires enabled=True and pro/enterprise tier)",
        ),
    )


def downgrade() -> None:
    """Remove auto_analyze column from github_repositories table."""
    op.drop_column("github_repositories", "auto_analyze")

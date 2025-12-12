"""Add repository_id link to github_repositories.

Revision ID: 020
Revises: 019
Create Date: 2025-12-12

This migration adds a foreign key from github_repositories to repositories,
linking the GitHub-specific repo data to the canonical Repository that
stores analysis runs and findings. This prevents data loss when repos
are disconnected/reconnected.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add repository_id column with FK to repositories table."""
    # Add the column
    op.add_column(
        "github_repositories",
        sa.Column(
            "repository_id",
            UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="SET NULL"),
            nullable=True,
            comment="Link to canonical Repository for analysis runs and findings",
        ),
    )

    # Add index for efficient lookups
    op.create_index(
        "ix_github_repositories_repository_id",
        "github_repositories",
        ["repository_id"],
    )

    # Backfill: link existing github_repositories to repositories by github_repo_id
    op.execute("""
        UPDATE github_repositories gr
        SET repository_id = r.id
        FROM repositories r
        WHERE gr.repo_id = r.github_repo_id
        AND gr.repository_id IS NULL
    """)


def downgrade() -> None:
    """Remove repository_id column."""
    op.drop_index("ix_github_repositories_repository_id", table_name="github_repositories")
    op.drop_column("github_repositories", "repository_id")

"""Add missing GitHub columns and tables.

Revision ID: 010
Revises: 009
Create Date: 2024-12-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add missing columns to github_installations and create github_repositories."""
    # Add missing columns to github_installations
    op.add_column(
        "github_installations",
        sa.Column("account_login", sa.String(255), nullable=True),
    )
    op.add_column(
        "github_installations",
        sa.Column("account_type", sa.String(50), nullable=True, server_default="Organization"),
    )
    op.create_index(
        "ix_github_installations_account_login",
        "github_installations",
        ["account_login"],
    )

    # Create github_repositories table
    op.create_table(
        "github_repositories",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("installation_id", sa.UUID(), sa.ForeignKey("github_installations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("default_branch", sa.String(255), server_default="main", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_github_repositories_installation_id", "github_repositories", ["installation_id"])
    op.create_index("ix_github_repositories_repo_id", "github_repositories", ["repo_id"])
    op.create_index("ix_github_repositories_full_name", "github_repositories", ["full_name"])
    op.create_index("ix_github_repositories_enabled", "github_repositories", ["enabled"])


def downgrade() -> None:
    """Remove github_repositories table and columns from github_installations."""
    op.drop_table("github_repositories")
    op.drop_index("ix_github_installations_account_login", table_name="github_installations")
    op.drop_column("github_installations", "account_type")
    op.drop_column("github_installations", "account_login")

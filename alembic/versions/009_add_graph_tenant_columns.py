"""Add graph tenant columns to organizations table.

Revision ID: 009
Revises: 008
Create Date: 2024-12-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add graph tenant columns to organizations table."""
    op.add_column(
        "organizations",
        sa.Column("graph_database_name", sa.String(100), nullable=True,
                  comment="Name of the graph database/graph for this organization"),
    )
    op.add_column(
        "organizations",
        sa.Column("graph_backend", sa.String(20), nullable=True, server_default="falkordb",
                  comment="Graph database backend: 'neo4j' or 'falkordb'"),
    )


def downgrade() -> None:
    """Remove graph tenant columns from organizations table."""
    op.drop_column("organizations", "graph_backend")
    op.drop_column("organizations", "graph_database_name")

"""Add seat_count column to subscriptions table

Revision ID: 003
Revises: 002
Create Date: 2024-11-30

Adds seat_count column for per-seat billing model.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add seat_count column with default value of 1
    op.add_column(
        "subscriptions",
        sa.Column("seat_count", sa.Integer(), server_default="1", nullable=False),
    )


def downgrade() -> None:
    # Remove seat_count column
    op.drop_column("subscriptions", "seat_count")

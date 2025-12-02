"""Add progress tracking and score fields to analysis_runs.

Revision ID: 006
Revises: 005
Create Date: 2024-12-02

Adds fields for:
- Progress tracking (progress_percent, current_step)
- Detailed scores (structure_score, quality_score, architecture_score)
- PR analysis (score_delta)
- File tracking (files_analyzed)
- Trigger tracking (triggered_by_id)
- Updated timestamp (updated_at)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add progress and score fields to analysis_runs."""
    # Add score breakdown fields
    op.add_column(
        "analysis_runs",
        sa.Column("structure_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("quality_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("architecture_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("score_delta", sa.Integer(), nullable=True),
    )

    # Add file tracking
    op.add_column(
        "analysis_runs",
        sa.Column("files_analyzed", sa.Integer(), nullable=False, server_default="0"),
    )

    # Add progress tracking fields
    op.add_column(
        "analysis_runs",
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "analysis_runs",
        sa.Column("current_step", sa.String(255), nullable=True),
    )

    # Add trigger tracking
    op.add_column(
        "analysis_runs",
        sa.Column("triggered_by_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_analysis_runs_triggered_by_id",
        "analysis_runs",
        "users",
        ["triggered_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_analysis_runs_triggered_by_id",
        "analysis_runs",
        ["triggered_by_id"],
    )

    # Add updated_at timestamp
    op.add_column(
        "analysis_runs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove progress and score fields from analysis_runs."""
    # Remove updated_at
    op.drop_column("analysis_runs", "updated_at")

    # Remove trigger tracking
    op.drop_index("ix_analysis_runs_triggered_by_id", table_name="analysis_runs")
    op.drop_constraint(
        "fk_analysis_runs_triggered_by_id", "analysis_runs", type_="foreignkey"
    )
    op.drop_column("analysis_runs", "triggered_by_id")

    # Remove progress tracking
    op.drop_column("analysis_runs", "current_step")
    op.drop_column("analysis_runs", "progress_percent")

    # Remove file tracking
    op.drop_column("analysis_runs", "files_analyzed")

    # Remove score fields
    op.drop_column("analysis_runs", "score_delta")
    op.drop_column("analysis_runs", "architecture_score")
    op.drop_column("analysis_runs", "quality_score")
    op.drop_column("analysis_runs", "structure_score")

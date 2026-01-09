"""Add status field to findings table.

Revision ID: 028
Revises: 027
Create Date: 2026-01-08

Adds workflow status tracking to findings:
- status: open, acknowledged, in_progress, resolved, wontfix, false_positive, duplicate
- status_reason: explanation for status change
- status_changed_by: user who changed status
- status_changed_at: when status was changed
- updated_at: general update timestamp
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add finding status columns and enum type."""
    # Create the finding_status enum type
    finding_status = postgresql.ENUM(
        "open",
        "acknowledged",
        "in_progress",
        "resolved",
        "wontfix",
        "false_positive",
        "duplicate",
        name="finding_status",
    )
    finding_status.create(op.get_bind(), checkfirst=True)

    # Add status column with default 'open'
    op.add_column(
        "findings",
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "acknowledged",
                "in_progress",
                "resolved",
                "wontfix",
                "false_positive",
                "duplicate",
                name="finding_status",
                create_type=False,
            ),
            nullable=False,
            server_default="open",
        ),
    )

    # Add status metadata columns
    op.add_column(
        "findings",
        sa.Column(
            "status_reason",
            sa.Text(),
            nullable=True,
            comment="Reason for status change (e.g., why marked as false positive)",
        ),
    )
    op.add_column(
        "findings",
        sa.Column(
            "status_changed_by",
            sa.String(255),
            nullable=True,
            comment="User ID who last changed the status",
        ),
    )
    op.add_column(
        "findings",
        sa.Column(
            "status_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When status was last changed",
        ),
    )

    # Add updated_at column
    op.add_column(
        "findings",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Create index on status for filtering
    op.create_index("ix_findings_status", "findings", ["status"], unique=False)


def downgrade() -> None:
    """Remove finding status columns and enum type."""
    # Drop index
    op.drop_index("ix_findings_status", table_name="findings")

    # Drop columns
    op.drop_column("findings", "updated_at")
    op.drop_column("findings", "status_changed_at")
    op.drop_column("findings", "status_changed_by")
    op.drop_column("findings", "status_reason")
    op.drop_column("findings", "status")

    # Drop the enum type
    finding_status = postgresql.ENUM(
        "open",
        "acknowledged",
        "in_progress",
        "resolved",
        "wontfix",
        "false_positive",
        "duplicate",
        name="finding_status",
    )
    finding_status.drop(op.get_bind(), checkfirst=True)

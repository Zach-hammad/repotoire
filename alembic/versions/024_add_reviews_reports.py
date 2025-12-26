"""Add asset security reviews and reports tables.

Revision ID: 024
Revises: 023
Create Date: 2025-01-15

REPO-385: Content Moderation & Review System

This migration adds:
- asset_security_reviews: Stores security scan results and review workflow
- asset_reports: Stores community reports for marketplace assets
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create asset_security_reviews table
    op.create_table(
        "asset_security_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("marketplace_asset_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer_id", sa.String(255), nullable=True),  # Clerk user ID
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            default="pending",
            server_default="pending",
        ),
        sa.Column("scan_findings", JSONB, nullable=True),
        sa.Column("scan_verdict", sa.String(50), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_notes", sa.Text, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changes_requested", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Create indexes for asset_security_reviews
    op.create_index(
        "ix_asset_security_reviews_asset_version_id",
        "asset_security_reviews",
        ["asset_version_id"],
    )
    op.create_index(
        "ix_asset_security_reviews_status",
        "asset_security_reviews",
        ["status"],
    )
    op.create_index(
        "ix_asset_security_reviews_reviewer_id",
        "asset_security_reviews",
        ["reviewer_id"],
    )
    op.create_index(
        "ix_asset_security_reviews_scanned_at",
        "asset_security_reviews",
        ["scanned_at"],
    )

    # Create asset_reports table
    op.create_table(
        "asset_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            UUID(as_uuid=True),
            sa.ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reporter_id", sa.String(255), nullable=False),  # Clerk user ID
        sa.Column(
            "reason",
            sa.String(50),
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            default="open",
            server_default="open",
        ),
        sa.Column("resolution_notes", sa.Text, nullable=True),
        sa.Column("resolved_by", sa.String(255), nullable=True),  # Clerk user ID
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )

    # Create indexes for asset_reports
    op.create_index(
        "ix_asset_reports_asset_id",
        "asset_reports",
        ["asset_id"],
    )
    op.create_index(
        "ix_asset_reports_reporter_id",
        "asset_reports",
        ["reporter_id"],
    )
    op.create_index(
        "ix_asset_reports_status",
        "asset_reports",
        ["status"],
    )
    op.create_index(
        "ix_asset_reports_reason",
        "asset_reports",
        ["reason"],
    )
    op.create_index(
        "ix_asset_reports_created_at",
        "asset_reports",
        ["created_at"],
    )

    # Create composite index for duplicate report prevention
    op.create_index(
        "ix_asset_reports_asset_reporter_open",
        "asset_reports",
        ["asset_id", "reporter_id", "status"],
    )


def downgrade() -> None:
    # Drop indexes for asset_reports
    op.drop_index("ix_asset_reports_asset_reporter_open", table_name="asset_reports")
    op.drop_index("ix_asset_reports_created_at", table_name="asset_reports")
    op.drop_index("ix_asset_reports_reason", table_name="asset_reports")
    op.drop_index("ix_asset_reports_status", table_name="asset_reports")
    op.drop_index("ix_asset_reports_reporter_id", table_name="asset_reports")
    op.drop_index("ix_asset_reports_asset_id", table_name="asset_reports")

    # Drop asset_reports table
    op.drop_table("asset_reports")

    # Drop indexes for asset_security_reviews
    op.drop_index(
        "ix_asset_security_reviews_scanned_at", table_name="asset_security_reviews"
    )
    op.drop_index(
        "ix_asset_security_reviews_reviewer_id", table_name="asset_security_reviews"
    )
    op.drop_index(
        "ix_asset_security_reviews_status", table_name="asset_security_reviews"
    )
    op.drop_index(
        "ix_asset_security_reviews_asset_version_id",
        table_name="asset_security_reviews",
    )

    # Drop asset_security_reviews table
    op.drop_table("asset_security_reviews")

"""Add GDPR compliance fields and tables

Revision ID: 004
Revises: 003
Create Date: 2024-12-02

Adds GDPR compliance features:
- deleted_at, anonymized_at, deletion_requested_at columns to users table
- data_exports table for tracking data export requests (Right to Access)
- consent_records table for tracking user consent preferences
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types for GDPR models
    export_status_enum = postgresql.ENUM(
        "pending", "processing", "completed", "failed", "expired",
        name="export_status",
        create_type=False,
    )
    export_status_enum.create(op.get_bind(), checkfirst=True)

    consent_type_enum = postgresql.ENUM(
        "essential", "analytics", "marketing",
        name="consent_type",
        create_type=False,
    )
    consent_type_enum.create(op.get_bind(), checkfirst=True)

    # Add GDPR columns to users table
    op.add_column(
        "users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])

    # Create data_exports table
    op.create_table(
        "data_exports",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "processing", "completed", "failed", "expired",
                name="export_status",
                create_type=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("download_url", sa.String(2048), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_data_exports_user_id", "data_exports", ["user_id"])
    op.create_index("ix_data_exports_status", "data_exports", ["status"])
    op.create_index("ix_data_exports_expires_at", "data_exports", ["expires_at"])

    # Create consent_records table
    op.create_table(
        "consent_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "consent_type",
            postgresql.ENUM(
                "essential", "analytics", "marketing",
                name="consent_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_consent_records_user_id", "consent_records", ["user_id"])
    op.create_index(
        "ix_consent_records_user_type",
        "consent_records",
        ["user_id", "consent_type"],
    )


def downgrade() -> None:
    # Drop consent_records table
    op.drop_index("ix_consent_records_user_type", table_name="consent_records")
    op.drop_index("ix_consent_records_user_id", table_name="consent_records")
    op.drop_table("consent_records")

    # Drop data_exports table
    op.drop_index("ix_data_exports_expires_at", table_name="data_exports")
    op.drop_index("ix_data_exports_status", table_name="data_exports")
    op.drop_index("ix_data_exports_user_id", table_name="data_exports")
    op.drop_table("data_exports")

    # Remove GDPR columns from users table
    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_column("users", "deletion_requested_at")
    op.drop_column("users", "anonymized_at")
    op.drop_column("users", "deleted_at")

    # Drop enum types
    postgresql.ENUM(name="consent_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="export_status").drop(op.get_bind(), checkfirst=True)

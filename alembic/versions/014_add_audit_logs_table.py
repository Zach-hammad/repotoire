"""Add audit_logs table for hybrid audit logging.

Revision ID: 014
Revises: 013
Create Date: 2024-12-04

This migration creates the audit_logs table for tracking both Clerk-sourced
authentication events and application-sourced business events. Designed for
SOC 2 and GDPR compliance with 2-year default retention.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create audit_logs table with indexes for common query patterns."""
    # Create enum types
    op.execute("CREATE TYPE event_source AS ENUM ('clerk', 'application')")
    op.execute("CREATE TYPE audit_status AS ENUM ('success', 'failure')")

    # Create audit_logs table
    op.create_table(
        "audit_logs",
        # Primary key
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # Timestamp - indexed for time-range queries
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
        # Event classification
        sa.Column(
            "event_type",
            sa.String(100),
            nullable=False,
            index=True,
            comment="Event type (e.g., 'user.login', 'repo.connected')",
        ),
        sa.Column(
            "event_source",
            sa.Enum("clerk", "application", name="event_source", create_type=False),
            nullable=False,
            comment="Whether event originated from Clerk webhook or application",
        ),
        # Actor information (who performed the action)
        sa.Column(
            "actor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
            comment="User who performed the action (null for system events)",
        ),
        sa.Column(
            "actor_email",
            sa.String(255),
            nullable=True,
            comment="Denormalized email for retention after user deletion",
        ),
        sa.Column(
            "actor_ip",
            sa.String(45),
            nullable=True,
            comment="IP address (supports IPv6)",
        ),
        sa.Column(
            "actor_user_agent",
            sa.String(1024),
            nullable=True,
            comment="Browser/client user agent string",
        ),
        # Organization context
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
            comment="Organization context for the action",
        ),
        # Resource information (what was acted upon)
        sa.Column(
            "resource_type",
            sa.String(100),
            nullable=True,
            comment="Type of resource (e.g., 'repository', 'analysis')",
        ),
        sa.Column(
            "resource_id",
            sa.String(255),
            nullable=True,
            comment="ID of the affected resource",
        ),
        # Action details
        sa.Column(
            "action",
            sa.String(100),
            nullable=True,
            comment="Action performed (e.g., 'created', 'updated', 'deleted')",
        ),
        sa.Column(
            "status",
            sa.Enum("success", "failure", name="audit_status", create_type=False),
            nullable=False,
            default="success",
            comment="Whether the action succeeded or failed",
        ),
        # Flexible metadata storage
        sa.Column(
            "metadata",
            JSONB,
            nullable=True,
            server_default="{}",
            comment="Additional context as JSON (changes, error details, etc.)",
        ),
        # Deduplication for Clerk webhooks
        sa.Column(
            "clerk_event_id",
            sa.String(255),
            nullable=True,
            unique=True,
            comment="Clerk event ID for webhook deduplication",
        ),
    )

    # Create composite indexes for common query patterns
    op.create_index(
        "ix_audit_logs_org_timestamp",
        "audit_logs",
        ["organization_id", "timestamp"],
        postgresql_using="btree",
    )
    op.create_index(
        "ix_audit_logs_actor_timestamp",
        "audit_logs",
        ["actor_id", "timestamp"],
        postgresql_using="btree",
    )
    op.create_index(
        "ix_audit_logs_resource",
        "audit_logs",
        ["resource_type", "resource_id"],
        postgresql_using="btree",
    )

    # Create partial index for Clerk events (deduplication lookup)
    op.create_index(
        "ix_audit_logs_clerk_event_id",
        "audit_logs",
        ["clerk_event_id"],
        unique=True,
        postgresql_where=sa.text("clerk_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove audit_logs table and enum types."""
    # Drop indexes
    op.drop_index("ix_audit_logs_clerk_event_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_timestamp", table_name="audit_logs")
    op.drop_index("ix_audit_logs_org_timestamp", table_name="audit_logs")

    # Drop table
    op.drop_table("audit_logs")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS audit_status")
    op.execute("DROP TYPE IF EXISTS event_source")

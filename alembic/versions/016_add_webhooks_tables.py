"""Add webhooks and webhook_deliveries tables.

Revision ID: 016
Revises: 015
Create Date: 2024-12-10

This migration creates the webhooks and webhook_deliveries tables for
customer webhook delivery system. Allows organizations to receive
real-time notifications about analysis events.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create webhooks and webhook_deliveries tables with indexes."""
    # Create delivery_status enum
    delivery_status_enum = postgresql.ENUM(
        "pending", "success", "failed", "retrying",
        name="delivery_status",
        create_type=False,
    )
    delivery_status_enum.create(op.get_bind(), checkfirst=True)

    # Create webhooks table
    op.create_table(
        "webhooks",
        # Primary key
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # Organization relationship
        sa.Column(
            "organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Webhook configuration
        sa.Column(
            "name",
            sa.String(255),
            nullable=False,
            comment="Human-readable name for the webhook",
        ),
        sa.Column(
            "url",
            sa.String(2048),
            nullable=False,
            comment="HTTPS URL to deliver webhooks to",
        ),
        sa.Column(
            "secret",
            sa.String(64),
            nullable=False,
            comment="HMAC-SHA256 secret for signature verification",
        ),
        sa.Column(
            "events",
            JSONB,
            nullable=False,
            server_default="[]",
            comment="List of subscribed event types",
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            default=True,
            server_default="true",
        ),
        sa.Column(
            "repository_ids",
            JSONB,
            nullable=True,
            comment="Optional list of repository IDs to filter events",
        ),
        # Timestamps
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

    # Create composite index for org + active status
    op.create_index(
        "ix_webhooks_org_active",
        "webhooks",
        ["organization_id", "is_active"],
        postgresql_using="btree",
    )

    # Create webhook_deliveries table
    op.create_table(
        "webhook_deliveries",
        # Primary key
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # Webhook relationship
        sa.Column(
            "webhook_id",
            UUID(as_uuid=True),
            sa.ForeignKey("webhooks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # Event info
        sa.Column(
            "event_type",
            sa.String(100),
            nullable=False,
            index=True,
            comment="Type of event being delivered",
        ),
        sa.Column(
            "payload",
            JSONB,
            nullable=False,
            comment="JSON payload sent to the webhook",
        ),
        # Delivery tracking
        sa.Column(
            "status",
            delivery_status_enum,
            nullable=False,
            default="pending",
            server_default="pending",
        ),
        sa.Column(
            "attempt_count",
            sa.Integer,
            nullable=False,
            default=0,
            server_default="0",
        ),
        sa.Column(
            "max_attempts",
            sa.Integer,
            nullable=False,
            default=5,
            server_default="5",
        ),
        # Response tracking
        sa.Column(
            "response_status_code",
            sa.Integer,
            nullable=True,
            comment="HTTP status code from the webhook endpoint",
        ),
        sa.Column(
            "response_body",
            sa.Text,
            nullable=True,
            comment="Response body (truncated to 1000 chars)",
        ),
        sa.Column(
            "error_message",
            sa.Text,
            nullable=True,
            comment="Error message if delivery failed",
        ),
        # Timing
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the delivery succeeded",
        ),
        sa.Column(
            "next_retry_at",
            sa.DateTime(timezone=True),
            nullable=True,
            index=True,
            comment="When the next retry attempt will be made",
        ),
    )

    # Create composite indexes for delivery queries
    op.create_index(
        "ix_webhook_deliveries_status_retry",
        "webhook_deliveries",
        ["status", "next_retry_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "ix_webhook_deliveries_created_at",
        "webhook_deliveries",
        ["created_at"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    """Remove webhooks and webhook_deliveries tables."""
    # Drop indexes
    op.drop_index("ix_webhook_deliveries_created_at", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_status_retry", table_name="webhook_deliveries")
    op.drop_index("ix_webhooks_org_active", table_name="webhooks")

    # Drop tables
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS delivery_status")

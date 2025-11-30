"""Add billing tables for Stripe integration

Revision ID: 002
Revises: 001
Create Date: 2024-11-30

Creates billing-related tables:
- subscriptions: Stripe subscription records linked to organizations
- usage_records: Monthly usage tracking for plan limit enforcement
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create subscription_status enum type
    subscription_status_enum = postgresql.ENUM(
        "active",
        "past_due",
        "canceled",
        "trialing",
        "incomplete",
        "incomplete_expired",
        "unpaid",
        "paused",
        name="subscription_status",
        create_type=False,
    )
    subscription_status_enum.create(op.get_bind(), checkfirst=True)

    # Create subscriptions table
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.UUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("stripe_subscription_id", sa.String(255), unique=True, nullable=False),
        sa.Column("stripe_price_id", sa.String(255), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "active",
                "past_due",
                "canceled",
                "trialing",
                "incomplete",
                "incomplete_expired",
                "unpaid",
                "paused",
                name="subscription_status",
                create_type=False,
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancel_at_period_end", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trial_end", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_subscriptions_organization_id", "subscriptions", ["organization_id"])
    op.create_index("ix_subscriptions_stripe_subscription_id", "subscriptions", ["stripe_subscription_id"])
    op.create_index("ix_subscriptions_status", "subscriptions", ["status"])

    # Create usage_records table
    op.create_table(
        "usage_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.UUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("repos_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("analyses_count", sa.Integer(), server_default="0", nullable=False),
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
    op.create_unique_constraint(
        "uq_usage_record_org_period",
        "usage_records",
        ["organization_id", "period_start"],
    )
    op.create_index("ix_usage_records_organization_id", "usage_records", ["organization_id"])
    op.create_index("ix_usage_records_period_start", "usage_records", ["period_start"])


def downgrade() -> None:
    # Drop tables
    op.drop_table("usage_records")
    op.drop_table("subscriptions")

    # Drop enum type
    postgresql.ENUM(name="subscription_status").drop(op.get_bind(), checkfirst=True)

"""Add Stripe Connect tables for marketplace creator payouts.

Revision ID: 022
Revises: 021
Create Date: 2025-12-19

Adds:
- stripe_account_id and connect status columns to marketplace_publishers
- marketplace_purchases table for tracking paid asset purchases

Stripe Connect enables marketplace creators to receive payouts when users
purchase their paid assets. The platform takes a 15% fee, and creators
receive 85% of the purchase price.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # Add Stripe Connect columns to marketplace_publishers
    # =========================================================================
    op.add_column(
        "marketplace_publishers",
        sa.Column("stripe_account_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "marketplace_publishers",
        sa.Column(
            "stripe_onboarding_complete",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "marketplace_publishers",
        sa.Column(
            "stripe_charges_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "marketplace_publishers",
        sa.Column(
            "stripe_payouts_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # Create unique index on stripe_account_id
    op.create_index(
        "ix_marketplace_publishers_stripe_account_id",
        "marketplace_publishers",
        ["stripe_account_id"],
        unique=True,
    )

    # =========================================================================
    # Create marketplace_purchases table
    # =========================================================================
    op.create_table(
        "marketplace_purchases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("platform_fee_cents", sa.Integer(), nullable=False),
        sa.Column("creator_share_cents", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(3),
            nullable=False,
            server_default="usd",
        ),
        sa.Column("stripe_payment_intent_id", sa.String(255), nullable=True),
        sa.Column("stripe_charge_id", sa.String(255), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refund_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["marketplace_assets.id"],
            ondelete="CASCADE",
        ),
        # One purchase per user per asset
        sa.UniqueConstraint(
            "asset_id",
            "user_id",
            name="uq_marketplace_purchases_asset_user",
        ),
        # Status validation
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'refunded')",
            name="ck_marketplace_purchases_status",
        ),
        # Amount validation
        sa.CheckConstraint(
            "amount_cents > 0",
            name="ck_marketplace_purchases_amount_positive",
        ),
        sa.CheckConstraint(
            "platform_fee_cents >= 0",
            name="ck_marketplace_purchases_fee_positive",
        ),
        sa.CheckConstraint(
            "creator_share_cents >= 0",
            name="ck_marketplace_purchases_share_positive",
        ),
    )

    # Create indexes
    op.create_index(
        "ix_marketplace_purchases_asset_id",
        "marketplace_purchases",
        ["asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_purchases_user_id",
        "marketplace_purchases",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_purchases_status",
        "marketplace_purchases",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_purchases_created_at",
        "marketplace_purchases",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_marketplace_purchases_stripe_payment_intent_id",
        "marketplace_purchases",
        ["stripe_payment_intent_id"],
        unique=True,
    )
    op.create_index(
        "ix_marketplace_purchases_stripe_charge_id",
        "marketplace_purchases",
        ["stripe_charge_id"],
        unique=True,
    )


def downgrade() -> None:
    # Drop marketplace_purchases table
    op.drop_index(
        "ix_marketplace_purchases_stripe_charge_id",
        table_name="marketplace_purchases",
    )
    op.drop_index(
        "ix_marketplace_purchases_stripe_payment_intent_id",
        table_name="marketplace_purchases",
    )
    op.drop_index(
        "ix_marketplace_purchases_created_at",
        table_name="marketplace_purchases",
    )
    op.drop_index(
        "ix_marketplace_purchases_status",
        table_name="marketplace_purchases",
    )
    op.drop_index(
        "ix_marketplace_purchases_user_id",
        table_name="marketplace_purchases",
    )
    op.drop_index(
        "ix_marketplace_purchases_asset_id",
        table_name="marketplace_purchases",
    )
    op.drop_table("marketplace_purchases")

    # Drop Stripe Connect columns from marketplace_publishers
    op.drop_index(
        "ix_marketplace_publishers_stripe_account_id",
        table_name="marketplace_publishers",
    )
    op.drop_column("marketplace_publishers", "stripe_payouts_enabled")
    op.drop_column("marketplace_publishers", "stripe_charges_enabled")
    op.drop_column("marketplace_publishers", "stripe_onboarding_complete")
    op.drop_column("marketplace_publishers", "stripe_account_id")

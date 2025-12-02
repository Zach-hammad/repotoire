"""Billing and subscription models for Stripe integration.

This module defines models for managing Stripe subscriptions, usage tracking,
customer add-ons, and billing-related data for the multi-tenant SaaS platform.
"""

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .organization import Organization


class AddonType(str, enum.Enum):
    """Types of purchasable add-ons."""

    BEST_OF_N = "best_of_n"  # Best-of-N sampling for auto-fix
    # Future add-ons can be added here
    # ADVANCED_SECURITY = "advanced_security"
    # CUSTOM_RULES = "custom_rules"


class SubscriptionStatus(str, enum.Enum):
    """Status of a Stripe subscription."""

    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    UNPAID = "unpaid"
    PAUSED = "paused"


class Subscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Stripe subscription record for an organization.

    Attributes:
        id: UUID primary key
        organization_id: Foreign key to the organization
        stripe_subscription_id: Unique Stripe subscription ID
        stripe_price_id: Stripe price ID for the subscription item
        status: Current subscription status
        current_period_start: Start of current billing period
        current_period_end: End of current billing period
        cancel_at_period_end: Whether subscription cancels at period end
        canceled_at: When the subscription was canceled (if applicable)
        trial_start: Start of trial period (if applicable)
        trial_end: End of trial period (if applicable)
        created_at: When the record was created
        updated_at: When the record was last updated
        organization: The organization this subscription belongs to
    """

    __tablename__ = "subscriptions"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # One subscription per organization
    )
    stripe_subscription_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    stripe_price_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(
            SubscriptionStatus,
            name="subscription_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=SubscriptionStatus.ACTIVE,
        nullable=False,
    )
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    trial_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    trial_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Seat-based billing
    seat_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="subscription",
    )

    __table_args__ = (
        Index("ix_subscriptions_organization_id", "organization_id"),
        Index("ix_subscriptions_status", "status"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "organization_id", "status")

    @property
    def is_active(self) -> bool:
        """Check if subscription is in an active state."""
        return self.status in (
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.TRIALING,
        )


class UsageRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Monthly usage tracking for an organization.

    Tracks usage metrics per billing period to enforce plan limits
    and provide usage insights.

    Attributes:
        id: UUID primary key
        organization_id: Foreign key to the organization
        period_start: First day of the billing period
        period_end: Last day of the billing period
        repos_count: Number of repositories connected
        analyses_count: Number of analyses run in the period
        created_at: When the record was created
        updated_at: When the record was last updated
        organization: The organization this usage record belongs to
    """

    __tablename__ = "usage_records"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    repos_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    analyses_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="usage_records",
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "period_start",
            name="uq_usage_record_org_period",
        ),
        Index("ix_usage_records_organization_id", "organization_id"),
        Index("ix_usage_records_period_start", "period_start"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "organization_id", "period_start")


class CustomerAddon(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Customer add-on purchases for premium features.

    Add-ons are tier-specific enhancements that can be purchased separately.
    For example, Pro tier customers can purchase the Best-of-N add-on.

    Attributes:
        id: UUID primary key
        customer_id: Customer identifier (organization ID or Stripe customer ID)
        addon_type: Type of add-on purchased
        is_active: Whether the add-on is currently active
        stripe_subscription_id: Stripe subscription ID for recurring billing
        price_monthly: Monthly price of the add-on
        activated_at: When the add-on was activated
        cancelled_at: When the add-on was cancelled (if applicable)
        created_at: When the record was created
        updated_at: When the record was last updated
    """

    __tablename__ = "customer_addons"

    customer_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    addon_type: Mapped[AddonType] = mapped_column(
        Enum(
            AddonType,
            name="addon_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    stripe_subscription_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    price_monthly: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "customer_id",
            "addon_type",
            name="uq_customer_addon",
        ),
        Index("ix_customer_addons_customer_id", "customer_id"),
        Index("ix_customer_addons_addon_type", "addon_type"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "customer_id", "addon_type", "is_active")


class BestOfNUsage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Best-of-N usage tracking per customer per month.

    Tracks monthly usage of the Best-of-N feature for billing and quota
    enforcement purposes.

    Attributes:
        id: UUID primary key
        customer_id: Customer identifier
        month: First day of the month (for grouping)
        runs_count: Number of Best-of-N runs in the month
        candidates_generated: Total candidates generated
        sandbox_cost_usd: Total sandbox cost for the month
        created_at: When the record was created
        updated_at: When the record was last updated
    """

    __tablename__ = "best_of_n_usage"

    customer_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    month: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )
    runs_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    candidates_generated: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    sandbox_cost_usd: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "customer_id",
            "month",
            name="uq_best_of_n_usage_customer_month",
        ),
        Index("ix_best_of_n_usage_customer_month", "customer_id", "month"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "customer_id", "month", "runs_count")

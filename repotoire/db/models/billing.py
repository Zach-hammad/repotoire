"""Billing and subscription models for Stripe integration.

This module defines models for managing Stripe subscriptions, usage tracking,
and billing-related data for the multi-tenant SaaS platform.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
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

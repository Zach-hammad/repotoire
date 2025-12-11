"""Customer webhook models for event delivery.

This module defines the Webhook and WebhookDelivery models for allowing
organizations to receive real-time notifications about analysis events.

Following patterns from:
- repotoire/db/models/audit.py (enum patterns, JSONB metadata)
- repotoire/db/models/organization.py (relationships, constraints)

Usage:
    from repotoire.db.models import Webhook, WebhookDelivery, WebhookEvent

    # Create a webhook endpoint
    webhook = Webhook(
        organization_id=org.id,
        name="My CI/CD Webhook",
        url="https://example.com/webhook",
        secret="abc123...",
        events=[WebhookEvent.ANALYSIS_COMPLETED.value],
    )
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any, List
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .organization import Organization


class WebhookEvent(str, enum.Enum):
    """Supported webhook event types."""

    ANALYSIS_STARTED = "analysis.started"
    ANALYSIS_COMPLETED = "analysis.completed"
    ANALYSIS_FAILED = "analysis.failed"
    HEALTH_SCORE_CHANGED = "health_score.changed"
    FINDING_NEW = "finding.new"
    FINDING_RESOLVED = "finding.resolved"


class DeliveryStatus(str, enum.Enum):
    """Status of a webhook delivery attempt."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"


class Webhook(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Customer-configured webhook endpoint.

    Attributes:
        id: UUID primary key
        organization_id: Foreign key to the organization
        name: Human-readable name for the webhook
        url: HTTPS URL to deliver webhooks to
        secret: Secret for HMAC-SHA256 signature verification
        events: List of event types to subscribe to
        is_active: Whether the webhook is enabled
        repository_ids: Optional list of repository IDs to filter events
        created_at: When the webhook was created
        updated_at: When the webhook was last updated

    Example:
        >>> webhook = Webhook(
        ...     organization_id=org.id,
        ...     name="CI/CD Integration",
        ...     url="https://ci.example.com/webhook",
        ...     secret=secrets.token_hex(32),
        ...     events=["analysis.completed", "analysis.failed"],
        ... )
    """

    __tablename__ = "webhooks"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    secret: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="HMAC-SHA256 secret for signature verification",
    )
    events: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment="List of subscribed event types",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Optional filtering by repository
    repository_ids: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Optional list of repository IDs to filter events",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="webhooks",
    )
    deliveries: Mapped[List["WebhookDelivery"]] = relationship(
        "WebhookDelivery",
        back_populates="webhook",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_webhooks_org_active", "organization_id", "is_active"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "name", "organization_id", "is_active")


class WebhookDelivery(Base, UUIDPrimaryKeyMixin):
    """Record of a webhook delivery attempt.

    Tracks each delivery attempt including payload, response, and retry state.

    Attributes:
        id: UUID primary key
        webhook_id: Foreign key to the webhook endpoint
        event_type: Type of event being delivered
        payload: JSON payload sent to the webhook
        status: Current delivery status (pending, success, failed, retrying)
        attempt_count: Number of delivery attempts made
        max_attempts: Maximum attempts before giving up (default: 5)
        response_status_code: HTTP status code from the webhook endpoint
        response_body: Response body (truncated to 1000 chars)
        error_message: Error message if delivery failed
        created_at: When the delivery was created
        delivered_at: When the delivery succeeded
        next_retry_at: When the next retry attempt will be made

    Example:
        >>> delivery = WebhookDelivery(
        ...     webhook_id=webhook.id,
        ...     event_type="analysis.completed",
        ...     payload={"event": "analysis.completed", "data": {...}},
        ... )
    """

    __tablename__ = "webhook_deliveries"

    webhook_id: Mapped[UUID] = mapped_column(
        ForeignKey("webhooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )

    # Delivery tracking
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(
            DeliveryStatus,
            name="delivery_status",
            values_callable=lambda x: [e.value for e in x],
            create_constraint=False,
        ),
        default=DeliveryStatus.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        default=5,
        nullable=False,
    )

    # Response tracking
    response_status_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    response_body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Response body (truncated to 1000 chars)",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Timing
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    # Relationships
    webhook: Mapped["Webhook"] = relationship(
        "Webhook",
        back_populates="deliveries",
    )

    __table_args__ = (
        Index("ix_webhook_deliveries_status_retry", "status", "next_retry_at"),
        Index("ix_webhook_deliveries_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "webhook_id", "event_type", "status")

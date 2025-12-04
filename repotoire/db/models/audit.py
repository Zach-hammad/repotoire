"""Audit log model for hybrid event tracking.

This module defines the AuditLog model for tracking both authentication events
from Clerk webhooks and application-sourced business events. Designed for
SOC 2 and GDPR compliance requirements.

Usage:
    from repotoire.db.models import AuditLog, EventSource, AuditStatus

    # Create an audit log entry
    log = AuditLog(
        event_type="user.login",
        event_source=EventSource.CLERK,
        actor_id=user.id,
        actor_email=user.email,
        status=AuditStatus.SUCCESS,
    )
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .organization import Organization
    from .user import User


class EventSource(str, enum.Enum):
    """Source of the audit event."""

    CLERK = "clerk"
    APPLICATION = "application"


class AuditStatus(str, enum.Enum):
    """Status of the audited action."""

    SUCCESS = "success"
    FAILURE = "failure"


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Audit log entry for tracking user and system actions.

    This model captures both Clerk-sourced authentication events and
    application-sourced business events for compliance and security monitoring.

    Attributes:
        id: UUID primary key
        timestamp: When the event occurred (indexed for time-range queries)
        event_type: Type of event (e.g., "user.login", "repo.connected")
        event_source: Whether event came from Clerk webhook or application
        actor_id: UUID of the user who performed the action (nullable for system events)
        actor_email: Denormalized email for retention after user deletion
        actor_ip: IP address of the actor (supports IPv6)
        actor_user_agent: Browser/client user agent string
        organization_id: Organization context for the action
        resource_type: Type of resource affected (e.g., "repository", "analysis")
        resource_id: ID of the affected resource
        action: Action performed (e.g., "created", "updated", "deleted")
        status: Whether the action succeeded or failed
        metadata: Additional context as JSON
        clerk_event_id: Clerk event ID for webhook deduplication

    Example:
        >>> log = AuditLog(
        ...     event_type="repo.connected",
        ...     event_source=EventSource.APPLICATION,
        ...     actor_id=user.id,
        ...     actor_email=user.email,
        ...     organization_id=org.id,
        ...     resource_type="repository",
        ...     resource_id=str(repo.id),
        ...     action="created",
        ...     status=AuditStatus.SUCCESS,
        ...     metadata={"repo_name": "owner/repo"},
        ... )
    """

    __tablename__ = "audit_logs"

    # Timestamp - indexed for time-range queries
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Event classification
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    event_source: Mapped[EventSource] = mapped_column(
        Enum(
            EventSource,
            name="event_source",
            values_callable=lambda x: [e.value for e in x],
            create_constraint=False,
        ),
        nullable=False,
    )

    # Actor information (who performed the action)
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    actor_ip: Mapped[str | None] = mapped_column(
        String(45),  # Supports IPv6
        nullable=True,
    )
    actor_user_agent: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )

    # Organization context
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Resource information (what was acted upon)
    resource_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Action details
    action: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    status: Mapped[AuditStatus] = mapped_column(
        Enum(
            AuditStatus,
            name="audit_status",
            values_callable=lambda x: [e.value for e in x],
            create_constraint=False,
        ),
        nullable=False,
        default=AuditStatus.SUCCESS,
    )

    # Flexible metadata storage (named event_metadata to avoid SQLAlchemy reserved name)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",  # Column name in database
        JSONB,
        nullable=True,
        server_default="{}",
    )

    # Deduplication for Clerk webhooks
    clerk_event_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
    )

    # Relationships
    actor: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[actor_id],
    )
    organization: Mapped["Organization | None"] = relationship(
        "Organization",
        foreign_keys=[organization_id],
    )

    __table_args__ = (
        # Composite indexes for common query patterns
        Index("ix_audit_logs_org_timestamp", "organization_id", "timestamp"),
        Index("ix_audit_logs_actor_timestamp", "actor_id", "timestamp"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    def __repr__(self) -> str:
        return generate_repr(
            self,
            "id",
            "event_type",
            "event_source",
            "actor_email",
            "timestamp",
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert audit log to dictionary for API responses.

        Returns:
            Dictionary representation of the audit log.
        """
        return {
            "id": str(self.id),
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "event_type": self.event_type,
            "event_source": self.event_source.value,
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "actor_email": self.actor_email,
            "actor_ip": self.actor_ip,
            "organization_id": str(self.organization_id) if self.organization_id else None,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "action": self.action,
            "status": self.status.value,
            "metadata": self.event_metadata,
        }

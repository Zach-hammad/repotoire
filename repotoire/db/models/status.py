"""Status page models for public service health tracking.

This module provides models for tracking service component status,
incidents, scheduled maintenance, and status page subscribers.
"""

from __future__ import annotations

import enum
import secrets
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .uptime import UptimeRecord


# =============================================================================
# Enums
# =============================================================================


class ComponentStatus(str, enum.Enum):
    """Status levels for service components."""

    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    PARTIAL_OUTAGE = "partial_outage"
    MAJOR_OUTAGE = "major_outage"
    MAINTENANCE = "maintenance"


class IncidentStatus(str, enum.Enum):
    """Status of an incident lifecycle."""

    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class IncidentSeverity(str, enum.Enum):
    """Severity level of an incident."""

    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


# =============================================================================
# Junction Tables
# =============================================================================


# Many-to-many relationship between incidents and components
incident_components = Table(
    "incident_components",
    Base.metadata,
    Column(
        "incident_id",
        ForeignKey("incidents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "component_id",
        ForeignKey("status_components.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)

# Many-to-many relationship between scheduled maintenances and components
maintenance_components = Table(
    "maintenance_components",
    Base.metadata,
    Column(
        "maintenance_id",
        ForeignKey("scheduled_maintenances.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "component_id",
        ForeignKey("status_components.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


# =============================================================================
# Models
# =============================================================================


class StatusComponent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Service component for status tracking.

    Each component represents a service or subsystem that is monitored
    for health status. Components can have automatic health checks
    via health_check_url.

    Attributes:
        id: UUID primary key
        name: Unique name of the component (e.g., "API", "Database")
        description: Optional description of the component
        status: Current status (operational, degraded, etc.)
        health_check_url: Internal URL for automated health checks
        display_order: Order in which to display on status page
        is_critical: Whether this component is critical (affects overall status)
        last_checked_at: When the component was last health-checked
        response_time_ms: Response time from last health check
        uptime_percentage: 30-day rolling uptime percentage
        incidents: List of incidents affecting this component
        maintenances: List of scheduled maintenances for this component
        uptime_records: Historical uptime records for this component
    """

    __tablename__ = "status_components"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ComponentStatus] = mapped_column(
        Enum(
            ComponentStatus,
            name="component_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ComponentStatus.OPERATIONAL,
        nullable=False,
    )
    health_check_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uptime_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    # Relationships
    incidents: Mapped[List["Incident"]] = relationship(
        "Incident",
        secondary=incident_components,
        back_populates="affected_components",
    )
    maintenances: Mapped[List["ScheduledMaintenance"]] = relationship(
        "ScheduledMaintenance",
        secondary=maintenance_components,
        back_populates="affected_components",
    )
    uptime_records: Mapped[List["UptimeRecord"]] = relationship(
        "UptimeRecord",
        back_populates="component",
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("ix_status_components_display_order", "display_order"),)

    def __repr__(self) -> str:
        return generate_repr(self, "id", "name", "status")


class Incident(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Service incident tracking.

    Tracks outages, degradations, and other service incidents.
    Each incident can affect multiple components and has a timeline
    of updates.

    Attributes:
        id: UUID primary key
        title: Short title describing the incident
        status: Current status (investigating, identified, etc.)
        severity: Severity level (minor, major, critical)
        message: Initial incident description
        started_at: When the incident started
        resolved_at: When the incident was resolved (if resolved)
        postmortem_url: URL to postmortem document (after resolution)
        affected_components: Components affected by this incident
        updates: Timeline of incident updates
    """

    __tablename__ = "incidents"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(
            IncidentStatus,
            name="incident_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    severity: Mapped[IncidentSeverity] = mapped_column(
        Enum(
            IncidentSeverity,
            name="incident_severity",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    postmortem_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    affected_components: Mapped[List["StatusComponent"]] = relationship(
        "StatusComponent",
        secondary=incident_components,
        back_populates="incidents",
    )
    updates: Mapped[List["IncidentUpdate"]] = relationship(
        "IncidentUpdate",
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="IncidentUpdate.created_at.desc()",
    )

    __table_args__ = (
        Index("ix_incidents_status_started", "status", "started_at"),
        Index("ix_incidents_resolved_at", "resolved_at"),
    )

    @property
    def is_resolved(self) -> bool:
        """Check if the incident is resolved."""
        return self.status == IncidentStatus.RESOLVED

    def __repr__(self) -> str:
        return generate_repr(self, "id", "title", "status", "severity")


class IncidentUpdate(Base, UUIDPrimaryKeyMixin):
    """Update posted to an incident timeline.

    Each update represents a status change or informational update
    during an incident's lifecycle.

    Attributes:
        id: UUID primary key
        incident_id: FK to the parent incident
        status: Status at the time of this update
        message: Update message/description
        created_at: When the update was posted
        incident: Parent incident relationship
    """

    __tablename__ = "incident_updates"

    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(
            IncidentStatus,
            name="incident_status",
            values_callable=lambda x: [e.value for e in x],
            create_constraint=False,
        ),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    incident: Mapped["Incident"] = relationship(
        "Incident",
        back_populates="updates",
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "incident_id", "status")


class ScheduledMaintenance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Scheduled maintenance window.

    Tracks planned maintenance periods that may affect service
    availability. Users can be notified in advance.

    Attributes:
        id: UUID primary key
        title: Title of the maintenance
        description: Detailed description of what will happen
        scheduled_start: When maintenance is scheduled to begin
        scheduled_end: When maintenance is scheduled to end
        is_cancelled: Whether the maintenance was cancelled
        affected_components: Components affected by this maintenance
    """

    __tablename__ = "scheduled_maintenances"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    scheduled_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_cancelled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Relationships
    affected_components: Mapped[List["StatusComponent"]] = relationship(
        "StatusComponent",
        secondary=maintenance_components,
        back_populates="maintenances",
    )

    __table_args__ = (
        Index(
            "ix_scheduled_maintenance_dates",
            "scheduled_start",
            "scheduled_end",
        ),
    )

    @property
    def is_active(self) -> bool:
        """Check if maintenance is currently in progress."""
        if self.is_cancelled:
            return False
        now = datetime.now(self.scheduled_start.tzinfo)
        return self.scheduled_start <= now <= self.scheduled_end

    @property
    def is_upcoming(self) -> bool:
        """Check if maintenance is scheduled for the future."""
        if self.is_cancelled:
            return False
        now = datetime.now(self.scheduled_start.tzinfo)
        return now < self.scheduled_start

    def __repr__(self) -> str:
        return generate_repr(self, "id", "title", "scheduled_start")


class StatusSubscriber(Base, UUIDPrimaryKeyMixin):
    """Subscriber to status page updates.

    Users can subscribe to receive email notifications about
    incidents and maintenance windows.

    Attributes:
        id: UUID primary key
        email: Subscriber's email address (unique)
        is_verified: Whether email has been verified
        verification_token: Token for email verification
        unsubscribe_token: Token for unsubscribing
        subscribed_at: When email was verified and subscription activated
        created_at: When subscription was initiated
    """

    __tablename__ = "status_subscribers"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unsubscribe_token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=lambda: secrets.token_urlsafe(32),
    )
    subscribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_status_subscribers_email", "email"),)

    def __repr__(self) -> str:
        return generate_repr(self, "id", "email", "is_verified")

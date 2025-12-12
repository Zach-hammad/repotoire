"""API deprecation tracking model.

This module provides database models for tracking deprecated API endpoints
and their sunset timelines, enabling proactive customer communication and
usage monitoring.

Usage:
    from repotoire.db.models import APIDeprecation, DeprecationStatus

    # Create a new deprecation entry
    deprecation = APIDeprecation(
        endpoint="/repos",
        method="GET",
        version="v1",
        message="Use /api/v2/repositories instead",
        replacement_endpoint="/api/v2/repositories",
        deprecation_date=datetime(2025, 6, 1),
        sunset_date=datetime(2025, 12, 1),
    )
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DeprecationStatus(str, Enum):
    """Lifecycle status of a deprecated endpoint.

    Attributes:
        ANNOUNCED: Deprecation has been announced but headers not yet active
        DEPRECATED: Deprecation headers are being added to responses
        SUNSET: Endpoint returns 410 Gone responses
        REMOVED: Endpoint code has been deleted from the codebase
    """

    ANNOUNCED = "announced"
    DEPRECATED = "deprecated"
    SUNSET = "sunset"
    REMOVED = "removed"


class APIDeprecation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Track deprecated API endpoints and their sunset timeline.

    This model stores metadata about deprecated endpoints for:
    - Automated deprecation header injection
    - Customer notification about upcoming changes
    - Usage tracking to identify affected integrations
    - Sunset enforcement (returning 410 Gone)

    Attributes:
        endpoint: The deprecated endpoint path (e.g., "/repos", "/analysis/{id}")
        method: HTTP method (GET, POST, etc.)
        version: API version (v1, v2)
        status: Current deprecation status
        message: Human-readable deprecation message
        replacement_endpoint: URL of the successor endpoint
        announced_at: When deprecation was announced
        deprecation_date: When deprecation headers started appearing
        sunset_date: When endpoint will/did return 410 Gone
        removed_at: When endpoint code was deleted
        last_called_at: Last time endpoint was called
        call_count_since_deprecation: Number of calls since deprecation started
    """

    __tablename__ = "api_deprecations"

    # Endpoint identification
    endpoint: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        index=True,
        comment="Deprecated endpoint path (e.g., /repos, /analysis/{id})",
    )
    method: Mapped[str] = mapped_column(
        String(10),
        default="GET",
        comment="HTTP method (GET, POST, PUT, DELETE, etc.)",
    )
    version: Mapped[str] = mapped_column(
        String(10),
        default="v1",
        index=True,
        comment="API version (v1, v2, etc.)",
    )
    status: Mapped[DeprecationStatus] = mapped_column(
        default=DeprecationStatus.ANNOUNCED,
        index=True,
        comment="Current deprecation lifecycle status",
    )

    # Deprecation details
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Human-readable deprecation message for headers and notifications",
    )
    replacement_endpoint: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="URL of the successor endpoint (if available)",
    )

    # Timeline
    announced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="When the deprecation was publicly announced",
    )
    deprecation_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When deprecation headers started appearing in responses",
    )
    sunset_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="When the endpoint will/did start returning 410 Gone",
    )
    removed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the endpoint code was deleted from the codebase",
    )

    # Usage tracking
    last_called_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last time this deprecated endpoint was called",
    )
    call_count_since_deprecation: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="Number of calls since deprecation headers started appearing",
    )

    def __repr__(self) -> str:
        return f"<APIDeprecation {self.method} {self.version}{self.endpoint} status={self.status.value}>"

    @property
    def is_active(self) -> bool:
        """Check if deprecation headers should be active."""
        return self.status in (DeprecationStatus.DEPRECATED, DeprecationStatus.SUNSET)

    @property
    def is_sunset(self) -> bool:
        """Check if endpoint should return 410 Gone."""
        return self.status == DeprecationStatus.SUNSET

    @property
    def full_path(self) -> str:
        """Get the full API path including version."""
        return f"/api/{self.version}{self.endpoint}"


__all__ = ["APIDeprecation", "DeprecationStatus"]

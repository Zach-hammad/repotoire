"""GDPR compliance models for data export and consent tracking.

This module defines models required for GDPR compliance:
- DataExport: Tracks user data export requests (Right to Access)
- ConsentRecord: Tracks user consent preferences for data processing
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .user import User


class ExportStatus(str, enum.Enum):
    """Status of a data export request."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class ConsentType(str, enum.Enum):
    """Types of consent that can be granted."""

    ESSENTIAL = "essential"  # Required for service operation
    ANALYTICS = "analytics"  # Usage analytics (PostHog)
    MARKETING = "marketing"  # Marketing communications


class DataExport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Data export request tracking for GDPR Right to Access.

    Attributes:
        id: UUID primary key
        user_id: Foreign key to the user requesting export
        status: Current status of the export request
        download_url: S3/R2 presigned URL for downloading export (if completed)
        expires_at: When the export download link expires
        completed_at: When the export was completed
        error_message: Error message if export failed
        file_size_bytes: Size of the exported file in bytes
        created_at: When the export was requested
        updated_at: When the export status was last updated
    """

    __tablename__ = "data_exports"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[ExportStatus] = mapped_column(
        PgEnum(
            "pending", "processing", "completed", "failed", "expired",
            name="export_status",
            create_type=False,  # Type created by migration
        ),
        default=ExportStatus.PENDING,
        nullable=False,
    )
    download_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    file_size_bytes: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="data_exports",
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "user_id", "status")


class ConsentRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Consent record tracking for GDPR compliance.

    Records user consent for different types of data processing.
    Each consent type change creates a new record for audit trail.

    Attributes:
        id: UUID primary key
        user_id: Foreign key to the user who gave consent
        consent_type: Type of consent (essential, analytics, marketing)
        granted: Whether consent was granted (True) or revoked (False)
        ip_address: IP address when consent was recorded
        user_agent: Browser user agent when consent was recorded
        created_at: When the consent was recorded
        updated_at: When the record was last updated
    """

    __tablename__ = "consent_records"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    consent_type: Mapped[ConsentType] = mapped_column(
        PgEnum(
            "essential", "analytics", "marketing",
            name="consent_type",
            create_type=False,  # Type created by migration
        ),
        nullable=False,
    )
    granted: Mapped[bool] = mapped_column(
        nullable=False,
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(45),  # IPv6 max length
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="consent_records",
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "user_id", "consent_type", "granted")

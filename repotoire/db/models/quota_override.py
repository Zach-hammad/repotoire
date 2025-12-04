"""QuotaOverride model for persisted admin quota overrides with audit trail.

This module defines the QuotaOverride model that stores quota overrides
granted by admins with full audit trail for compliance and tracking.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .organization import Organization
    from .user import User


class QuotaOverrideType(str, enum.Enum):
    """Types of quotas that can be overridden."""

    SANDBOX_MINUTES = "sandbox_minutes"
    CONCURRENT_SESSIONS = "concurrent_sessions"
    STORAGE_GB = "storage_gb"
    ANALYSIS_PER_MONTH = "analysis_per_month"
    MAX_REPO_SIZE_MB = "max_repo_size_mb"
    DAILY_SANDBOX_MINUTES = "daily_sandbox_minutes"
    MONTHLY_SANDBOX_MINUTES = "monthly_sandbox_minutes"
    SANDBOXES_PER_DAY = "sandboxes_per_day"


class QuotaOverride(Base, UUIDPrimaryKeyMixin):
    """QuotaOverride model representing an admin-granted quota override.

    Stores quota overrides with full audit trail including who created it,
    why it was granted, optional expiration, and revocation information.

    Attributes:
        id: UUID primary key
        organization_id: Foreign key to the organization receiving the override
        override_type: Type of quota being overridden
        original_limit: What the tier limit was at time of override
        override_limit: New limit granted by the override
        reason: Why the override was granted (audit trail)
        created_by_id: Foreign key to the admin who created the override
        created_at: When the override was created
        expires_at: Optional expiration datetime (null = never expires)
        revoked_at: When the override was revoked (null = still active)
        revoked_by_id: Foreign key to the admin who revoked the override
        revoke_reason: Why the override was revoked
        organization: The organization receiving the override
        created_by: The admin who created the override
        revoked_by: The admin who revoked the override (if any)
    """

    __tablename__ = "quota_overrides"

    # Organization receiving the override
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # What's being overridden
    override_type: Mapped[QuotaOverrideType] = mapped_column(
        Enum(
            QuotaOverrideType,
            name="quota_override_type",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    original_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Original tier limit at time of override creation",
    )
    override_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="New limit granted by this override",
    )

    # Audit: Creation
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Why this override was granted",
    )
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
        comment="Admin who created this override",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Expiration (date-based, not TTL)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this override expires (null = never)",
    )

    # Audit: Revocation
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this override was revoked",
    )
    revoked_by_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Admin who revoked this override",
    )
    revoke_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Why this override was revoked",
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="quota_overrides",
    )
    created_by: Mapped["User"] = relationship(
        "User",
        foreign_keys=[created_by_id],
    )
    revoked_by: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[revoked_by_id],
    )

    __table_args__ = (
        # Fast lookup for active overrides by org and type
        Index("ix_quota_overrides_org_type", "organization_id", "override_type"),
        # Partial index for active overrides only (PostgreSQL)
        Index(
            "ix_quota_overrides_active",
            "organization_id",
            "override_type",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        # Audit queries by admin who created overrides
        Index("ix_quota_overrides_created_by", "created_by_id"),
        # Lookup by expiration for cleanup jobs
        Index("ix_quota_overrides_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:
        return generate_repr(
            self,
            "id",
            "organization_id",
            "override_type",
            "override_limit",
        )

    @property
    def is_active(self) -> bool:
        """Check if this override is currently active.

        An override is active if:
        - It has not been revoked (revoked_at is None)
        - It has not expired (expires_at is None or > now)
        """
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= datetime.utcnow():
            return False
        return True

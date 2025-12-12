"""User model for Clerk authentication integration.

This module defines the User model that maps Clerk's user identifiers
to internal user records with profile information.
"""

from datetime import datetime
from typing import TYPE_CHECKING, List

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .changelog import ChangelogEntry
    from .email import EmailPreferences
    from .gdpr import ConsentRecord, DataExport
    from .organization import OrganizationMembership


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User model representing an authenticated user from Clerk.

    Attributes:
        id: UUID primary key
        clerk_user_id: Unique identifier from Clerk authentication
        email: User's email address (unique)
        name: Display name (optional)
        avatar_url: URL to user's avatar image (optional)
        created_at: When the user was created
        updated_at: When the user was last updated
        deleted_at: When the user was soft deleted (GDPR)
        anonymized_at: When user data was anonymized (GDPR)
        deletion_requested_at: When deletion was requested (GDPR grace period)
        memberships: List of organization memberships for this user
        data_exports: List of data export requests for this user
        consent_records: List of consent records for this user
    """

    __tablename__ = "users"

    clerk_user_id: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    avatar_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    # GDPR: Soft delete and anonymization
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    anonymized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    memberships: Mapped[List["OrganizationMembership"]] = relationship(
        "OrganizationMembership",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    data_exports: Mapped[List["DataExport"]] = relationship(
        "DataExport",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    consent_records: Mapped[List["ConsentRecord"]] = relationship(
        "ConsentRecord",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    email_preferences: Mapped["EmailPreferences | None"] = relationship(
        "EmailPreferences",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    changelog_entries: Mapped[List["ChangelogEntry"]] = relationship(
        "ChangelogEntry",
        back_populates="author",
    )

    __table_args__ = (
        Index("ix_users_email", "email"),
    )

    @property
    def is_deleted(self) -> bool:
        """Check if the user has been soft deleted."""
        return self.deleted_at is not None

    @property
    def is_anonymized(self) -> bool:
        """Check if the user data has been anonymized."""
        return self.anonymized_at is not None

    @property
    def has_pending_deletion(self) -> bool:
        """Check if the user has a pending deletion request."""
        return self.deletion_requested_at is not None and not self.is_deleted

    def __repr__(self) -> str:
        return generate_repr(self, "id", "email")

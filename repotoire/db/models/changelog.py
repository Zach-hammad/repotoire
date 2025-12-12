"""Changelog models for public release notes and updates.

This module provides models for tracking changelog entries, subscribers,
and user read status for the "What's New" modal feature.
"""

from __future__ import annotations

import enum
import secrets
from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .user import User


# =============================================================================
# Enums
# =============================================================================


class ChangelogCategory(str, enum.Enum):
    """Categories for changelog entries."""

    FEATURE = "feature"
    IMPROVEMENT = "improvement"
    FIX = "fix"
    BREAKING = "breaking"
    SECURITY = "security"
    DEPRECATION = "deprecation"


class DigestFrequency(str, enum.Enum):
    """Frequency of changelog digest emails."""

    INSTANT = "instant"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# =============================================================================
# Models
# =============================================================================


class ChangelogEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A changelog entry for release notes.

    Supports both draft and published states, scheduled publishing,
    and categorization for filtering on the public changelog page.

    Attributes:
        id: UUID primary key
        version: Optional version string (e.g., "v1.2.0")
        title: Short title for the entry
        slug: URL-friendly unique identifier
        summary: Brief description for list views
        content: Full Markdown content
        category: Type of change (feature, fix, etc.)
        is_draft: Whether entry is visible publicly
        is_major: Highlight as major release
        published_at: When entry was published
        scheduled_for: Future publish date (auto-publish via Celery)
        author_id: FK to user who created the entry
        image_url: Optional hero image URL
        author: Relationship to User model
    """

    __tablename__ = "changelog_entries"

    version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[ChangelogCategory] = mapped_column(
        Enum(
            ChangelogCategory,
            name="changelog_category",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_major: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    author_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    author: Mapped["User | None"] = relationship(
        "User",
        back_populates="changelog_entries",
    )

    __table_args__ = (
        # Index for efficiently querying published entries
        Index(
            "ix_changelog_entries_published",
            "published_at",
            postgresql_where="is_draft = false",
        ),
        Index("ix_changelog_entries_category", "category"),
        # Index for finding scheduled entries to auto-publish
        Index(
            "ix_changelog_entries_scheduled",
            "scheduled_for",
            postgresql_where="is_draft = true AND scheduled_for IS NOT NULL",
        ),
    )

    @property
    def is_published(self) -> bool:
        """Check if the entry is published."""
        return not self.is_draft and self.published_at is not None

    @property
    def is_scheduled(self) -> bool:
        """Check if the entry is scheduled for future publication."""
        return self.is_draft and self.scheduled_for is not None

    def __repr__(self) -> str:
        return generate_repr(self, "id", "title", "category", "is_draft")


class ChangelogSubscriber(Base, UUIDPrimaryKeyMixin):
    """Subscriber to changelog updates via email.

    Supports email verification and configurable digest frequency
    (instant, weekly, monthly).

    Attributes:
        id: UUID primary key
        email: Subscriber's email address
        is_verified: Whether email has been verified
        verification_token: Token for email verification
        unsubscribe_token: Token for one-click unsubscribe
        digest_frequency: How often to receive updates
        subscribed_at: When subscription was verified
        created_at: When subscription was initiated
    """

    __tablename__ = "changelog_subscribers"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unsubscribe_token: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=lambda: secrets.token_urlsafe(32),
    )
    digest_frequency: Mapped[DigestFrequency] = mapped_column(
        Enum(
            DigestFrequency,
            name="digest_frequency",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=DigestFrequency.INSTANT,
        nullable=False,
    )
    subscribed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_changelog_subscribers_email", "email"),)

    def __repr__(self) -> str:
        return generate_repr(self, "id", "email", "is_verified", "digest_frequency")


class UserChangelogRead(Base, UUIDPrimaryKeyMixin):
    """Tracks when a user last read the changelog.

    Used for the "What's New" modal to show unread entries
    since the user's last visit.

    Attributes:
        id: UUID primary key
        user_id: FK to the user
        last_read_entry_id: Most recent entry the user has seen
        last_read_at: When the user last viewed the changelog
    """

    __tablename__ = "user_changelog_reads"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    last_read_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("changelog_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_changelog_reads_user_id"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "user_id", "last_read_at")

"""In-app notification model.

This module defines the InAppNotification model for storing
user notifications in the database.
"""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from .user import User


class NotificationType(str, Enum):
    """Types of in-app notifications."""

    ANALYSIS_COMPLETE = "analysis_complete"
    ANALYSIS_FAILED = "analysis_failed"
    NEW_FINDING = "new_finding"
    FIX_SUGGESTION = "fix_suggestion"
    HEALTH_REGRESSION = "health_regression"
    TEAM_INVITE = "team_invite"
    TEAM_ROLE_CHANGE = "team_role_change"
    BILLING_EVENT = "billing_event"
    SYSTEM = "system"


class InAppNotification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """In-app notification for a user.

    Attributes:
        id: UUID primary key.
        user_id: Foreign key to the user who should receive the notification.
        type: Type of notification (determines icon and styling).
        title: Short title for the notification.
        message: Full notification message.
        read: Whether the user has read the notification.
        read_at: Timestamp when the notification was marked as read.
        action_url: Optional URL for the notification action.
        extra_data: Additional context-specific data (repo name, finding count, etc.).
    """

    __tablename__ = "in_app_notifications"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=NotificationType.SYSTEM.value,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    action_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )

    extra_data: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON,
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="notifications",
    )

    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "read"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<InAppNotification id={self.id} user_id={self.user_id} type={self.type}>"

    def mark_as_read(self) -> None:
        """Mark this notification as read."""
        from datetime import timezone
        self.read = True
        self.read_at = datetime.now(timezone.utc)

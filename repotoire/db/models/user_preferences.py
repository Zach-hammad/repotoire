"""User preferences model.

This module defines the UserPreferences model for tracking user
preferences for appearance, notifications, and auto-fix behavior.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from .user import User


class UserPreferences(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User preferences for dashboard settings.

    Controls appearance, notification, and auto-fix preferences
    throughout the dashboard.

    Attributes:
        id: UUID primary key.
        user_id: Foreign key to the user.
        theme: Theme preference ('light', 'dark', 'system').
        new_fix_alerts: Enable alerts for new fixes.
        critical_security_alerts: Enable alerts for critical security fixes.
        weekly_summary: Enable weekly summary emails.
        auto_approve_high_confidence: Auto-approve high confidence fixes.
        generate_tests: Generate tests for applied fixes.
        create_git_branches: Create separate branches for each fix.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Appearance settings
    theme: Mapped[str] = mapped_column(
        String(20),
        default="system",
        nullable=False,
    )

    # Notification settings
    new_fix_alerts: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    critical_security_alerts: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    weekly_summary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Auto-fix settings
    auto_approve_high_confidence: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    generate_tests: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    create_git_branches: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="preferences",
    )

    def __repr__(self) -> str:
        return f"<UserPreferences user_id={self.user_id}>"

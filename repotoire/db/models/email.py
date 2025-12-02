"""Email notification preferences model.

This module defines the EmailPreferences model for tracking user
notification settings.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from .user import User


class EmailPreferences(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User email notification preferences.

    Attributes:
        id: UUID primary key.
        user_id: Foreign key to the user.
        analysis_complete: Notify when analysis completes successfully.
        analysis_failed: Notify when analysis fails.
        health_regression: Notify when health score drops significantly.
        weekly_digest: Send weekly summary email.
        team_notifications: Notify about team changes (invites, role changes).
        billing_notifications: Notify about billing events.
        regression_threshold: Minimum score drop to trigger regression alert.
    """

    __tablename__ = "email_preferences"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Notification toggles
    analysis_complete: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    analysis_failed: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    health_regression: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    weekly_digest: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    team_notifications: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    billing_notifications: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Threshold for regression alerts (only alert if score drops by this much)
    regression_threshold: Mapped[int] = mapped_column(
        Integer,
        default=10,
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="email_preferences",
    )

    def __repr__(self) -> str:
        return f"<EmailPreferences user_id={self.user_id}>"

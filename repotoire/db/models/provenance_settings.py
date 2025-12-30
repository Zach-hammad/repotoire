"""Provenance display settings model.

This module defines the ProvenanceSettings model for tracking user
preferences on how git provenance/attribution information is displayed.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from .user import User


class ProvenanceSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """User provenance display settings.

    Controls how git provenance/attribution information is displayed
    throughout the dashboard. Privacy-first defaults mean author
    information is hidden by default.

    Attributes:
        id: UUID primary key.
        user_id: Foreign key to the user.
        show_author_names: Display real author names (default: False for privacy).
        show_author_avatars: Display author avatars from Gravatar (default: False).
        show_confidence_badges: Display confidence level indicators (default: True).
        auto_query_provenance: Automatically load provenance on page load (default: False).
    """

    __tablename__ = "provenance_settings"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Privacy settings (defaults are privacy-first)
    show_author_names: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    show_author_avatars: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Display settings
    show_confidence_badges: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Performance settings
    auto_query_provenance: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="provenance_settings",
    )

    def __repr__(self) -> str:
        return f"<ProvenanceSettings user_id={self.user_id}>"

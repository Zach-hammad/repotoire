"""User model for Clerk authentication integration.

This module defines the User model that maps Clerk's user identifiers
to internal user records with profile information.
"""

from typing import TYPE_CHECKING, List
from uuid import UUID

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
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
        memberships: List of organization memberships for this user
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

    # Relationships
    memberships: Mapped[List["OrganizationMembership"]] = relationship(
        "OrganizationMembership",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_users_email", "email"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "email")

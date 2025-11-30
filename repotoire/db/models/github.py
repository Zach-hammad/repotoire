"""GitHubInstallation model for GitHub App integrations.

This module defines the GitHubInstallation model that tracks GitHub App
installations for organizations, managing access tokens for API access.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .organization import Organization


class GitHubInstallation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """GitHubInstallation model representing a GitHub App installation.

    Stores installation-level access tokens for API access to repositories.
    Tokens are encrypted at rest for security.

    Attributes:
        id: UUID primary key
        organization_id: Foreign key to the organization
        installation_id: GitHub App installation ID (unique)
        access_token_encrypted: Encrypted installation access token
        token_expires_at: When the current token expires
        suspended_at: When the installation was suspended (if applicable)
        created_at: When the installation was created
        updated_at: When the installation was last updated
        organization: The organization that owns this installation
    """

    __tablename__ = "github_installations"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    installation_id: Mapped[int] = mapped_column(
        Integer,
        unique=True,
        nullable=False,
    )
    access_token_encrypted: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    token_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    suspended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="github_installations",
    )

    __table_args__ = (
        Index("ix_github_installations_organization_id", "organization_id"),
        Index("ix_github_installations_installation_id", "installation_id"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "installation_id")

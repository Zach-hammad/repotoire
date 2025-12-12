"""GitHub App models for repository integrations.

This module defines models for GitHub App installations and repositories,
tracking access tokens, repository selection, and analysis configuration.
"""

from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .organization import Organization
    from .repository import Repository


class GitHubInstallation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """GitHubInstallation model representing a GitHub App installation.

    Stores installation-level access tokens for API access to repositories.
    Tokens are encrypted at rest for security using Fernet symmetric encryption.

    Attributes:
        id: UUID primary key
        organization_id: Foreign key to the organization
        installation_id: GitHub App installation ID (unique)
        account_login: GitHub account/organization login name
        account_type: Account type ("Organization" or "User")
        access_token_encrypted: Encrypted installation access token
        token_expires_at: When the current token expires
        suspended_at: When the installation was suspended (if applicable)
        created_at: When the installation was created
        updated_at: When the installation was last updated
        organization: The organization that owns this installation
        repositories: List of repositories for this installation
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
    account_login: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    account_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="Organization",
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
    repositories: Mapped[List["GitHubRepository"]] = relationship(
        "GitHubRepository",
        back_populates="installation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_github_installations_organization_id", "organization_id"),
        Index("ix_github_installations_installation_id", "installation_id"),
        Index("ix_github_installations_account_login", "account_login"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "installation_id", "account_login")


class GitHubRepository(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """GitHubRepository model representing a repository available for analysis.

    Tracks repositories accessible through a GitHub App installation,
    including whether they're enabled for analysis and when they were last analyzed.

    Attributes:
        id: UUID primary key
        installation_id: Foreign key to the GitHubInstallation
        repo_id: GitHub's repository ID
        full_name: Full repository name (e.g., "owner/repo")
        default_branch: Default branch name (e.g., "main")
        enabled: Whether this repository is enabled for analysis
        last_analyzed_at: When the repository was last analyzed
        created_at: When the repository was added
        updated_at: When the repository was last updated
        installation: The installation this repository belongs to
    """

    __tablename__ = "github_repositories"

    installation_id: Mapped[UUID] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    default_branch: Mapped[str] = mapped_column(
        String(255),
        default="main",
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    auto_analyze: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Whether to auto-analyze on push events (requires enabled=True and pro/enterprise tier)",
    )
    pr_analysis_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Whether to analyze pull requests",
    )
    quality_gates: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        comment="Quality gate configuration: {enabled, block_on_critical, block_on_high, min_health_score, max_new_issues}",
    )
    last_analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Link to canonical Repository for analysis data
    repository_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"),
        nullable=True,
        comment="Link to canonical Repository for analysis runs and findings",
    )

    # Relationships
    installation: Mapped["GitHubInstallation"] = relationship(
        "GitHubInstallation",
        back_populates="repositories",
    )
    repository: Mapped["Repository | None"] = relationship(
        "Repository",
        foreign_keys=[repository_id],
        lazy="joined",
    )

    __table_args__ = (
        Index("ix_github_repositories_installation_id", "installation_id"),
        Index("ix_github_repositories_repo_id", "repo_id"),
        Index("ix_github_repositories_full_name", "full_name"),
        Index("ix_github_repositories_enabled", "enabled"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "full_name", "enabled")

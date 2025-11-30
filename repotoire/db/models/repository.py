"""Repository model for GitHub repositories.

This module defines the Repository model that tracks GitHub repositories
connected to organizations for code health analysis.
"""

from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .analysis import AnalysisRun
    from .organization import Organization


class Repository(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Repository model representing a GitHub repository connected for analysis.

    Attributes:
        id: UUID primary key
        organization_id: Foreign key to the owning organization
        github_repo_id: GitHub's repository ID
        github_installation_id: GitHub App installation ID for API access
        full_name: Full repository name (e.g., "owner/repo")
        default_branch: Default branch name (e.g., "main")
        is_active: Whether the repository is actively monitored
        last_analyzed_at: When the repository was last analyzed
        health_score: Latest health score (0-100)
        created_at: When the repository was connected
        updated_at: When the repository was last updated
        organization: The organization that owns this repository
        analysis_runs: List of analysis runs for this repository
    """

    __tablename__ = "repositories"

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    github_repo_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        index=True,
    )
    github_installation_id: Mapped[int] = mapped_column(
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
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    last_analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    health_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="repositories",
    )
    analysis_runs: Mapped[List["AnalysisRun"]] = relationship(
        "AnalysisRun",
        back_populates="repository",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_repositories_organization_id", "organization_id"),
        Index("ix_repositories_full_name", "full_name"),
        Index("ix_repositories_github_installation_id", "github_installation_id"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "full_name")

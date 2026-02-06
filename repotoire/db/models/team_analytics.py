"""Team analytics models for code ownership and collaboration tracking.

This module defines models for team-based code analysis features:
- Developer: Individual contributor profiles
- CodeOwnership: File/function ownership based on git history
- Collaboration: Cross-developer collaboration metrics

These features are cloud-only and require organization membership.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from .organization import Organization
    from .repository import Repository


class Developer(Base, UUIDPrimaryKeyMixin):
    """Developer profile aggregated from git history.
    
    Tracks individual contributors across repositories in an organization.
    Derived from git commit author info (name, email).
    
    Attributes:
        organization_id: The organization this developer belongs to
        email: Primary git email (used for matching commits)
        name: Display name from git history
        aliases: Alternative emails/names for this developer (JSON list)
        first_commit_at: Date of first commit in org repos
        last_commit_at: Date of most recent commit
        total_commits: Total commit count across all repos
        total_lines_added: Total lines added across all repos
        total_lines_removed: Total lines removed across all repos
        expertise_areas: Top file patterns/directories (JSON object)
        linked_user_id: Optional link to a User account (for auth users)
    """
    
    __tablename__ = "developers"
    
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    aliases: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=list,
    )
    first_commit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_commit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    total_commits: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    total_lines_added: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    total_lines_removed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    expertise_areas: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
    )
    linked_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_developer_org_email"),
        Index("ix_developer_org_commits", "organization_id", "total_commits"),
    )


class OwnershipType(str, enum.Enum):
    """Type of code ownership."""
    
    FILE = "file"  # Ownership of entire file
    FUNCTION = "function"  # Ownership of specific function
    CLASS = "class"  # Ownership of specific class
    MODULE = "module"  # Ownership of module/directory


class CodeOwnership(Base, UUIDPrimaryKeyMixin):
    """Code ownership tracking based on git history.
    
    Tracks who "owns" specific parts of the codebase based on:
    - Recent commits (weighted by recency)
    - Lines of code written
    - Review activity
    
    Attributes:
        repository_id: The repository containing this code
        developer_id: The developer who owns this code
        ownership_type: Type of ownership (file, function, class)
        path: File path or qualified name
        ownership_score: Ownership strength (0.0-1.0)
        lines_owned: Lines of code attributed to this developer
        last_modified_at: When this developer last modified this code
        commit_count: Number of commits by this developer to this code
        extra_data: Additional ownership metadata (JSON)
    """
    
    __tablename__ = "code_ownership"
    
    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    developer_id: Mapped[UUID] = mapped_column(
        ForeignKey("developers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ownership_type: Mapped[OwnershipType] = mapped_column(
        Enum(
            OwnershipType,
            name="ownership_type",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
    )
    ownership_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )
    lines_owned: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    last_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    commit_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    extra_data: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    
    __table_args__ = (
        UniqueConstraint(
            "repository_id", "developer_id", "ownership_type", "path",
            name="uq_ownership_repo_dev_type_path"
        ),
        Index("ix_ownership_repo_path", "repository_id", "path"),
        Index("ix_ownership_dev_score", "developer_id", "ownership_score"),
    )


class Collaboration(Base, UUIDPrimaryKeyMixin):
    """Collaboration metrics between developers.
    
    Tracks how developers work together based on:
    - Co-authorship (commits to same files)
    - Code reviews
    - Handoffs (one developer modifying another's code)
    
    Attributes:
        organization_id: Organization context
        developer_a_id: First developer in the pair
        developer_b_id: Second developer in the pair
        collaboration_score: Strength of collaboration (0.0-1.0)
        shared_files: Number of files both have modified
        co_commits: Number of commits touching same files within time window
        reviews_given: Reviews A gave to B's code
        reviews_received: Reviews A received from B
        handoff_count: Times one modified code originally written by other
        last_interaction_at: Most recent collaboration timestamp
    """
    
    __tablename__ = "collaborations"
    
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    developer_a_id: Mapped[UUID] = mapped_column(
        ForeignKey("developers.id", ondelete="CASCADE"),
        nullable=False,
    )
    developer_b_id: Mapped[UUID] = mapped_column(
        ForeignKey("developers.id", ondelete="CASCADE"),
        nullable=False,
    )
    collaboration_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )
    shared_files: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    co_commits: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    reviews_given: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    reviews_received: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    handoff_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    last_interaction_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    
    __table_args__ = (
        # Ensure unique pair (order doesn't matter, but we'll enforce A < B)
        UniqueConstraint(
            "organization_id", "developer_a_id", "developer_b_id",
            name="uq_collaboration_org_devs"
        ),
        Index("ix_collaboration_dev_a", "developer_a_id"),
        Index("ix_collaboration_dev_b", "developer_b_id"),
    )


class TeamInsight(Base, UUIDPrimaryKeyMixin):
    """Aggregated team insights computed periodically.
    
    Pre-computed team-level metrics for dashboards.
    
    Attributes:
        organization_id: Organization context
        repository_id: Optional repository (null = org-wide)
        insight_type: Type of insight (e.g., "bus_factor", "knowledge_silos")
        insight_data: Computed insight data (JSON)
        computed_at: When this insight was computed
    """
    
    __tablename__ = "team_insights"
    
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    repository_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    insight_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    insight_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    
    __table_args__ = (
        Index("ix_team_insight_org_type", "organization_id", "insight_type"),
    )

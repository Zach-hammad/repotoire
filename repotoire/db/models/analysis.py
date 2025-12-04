"""AnalysisRun model for tracking code analysis jobs.

This module defines the AnalysisRun model that tracks the status and
results of code health analysis runs for repositories.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, List
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .finding import Finding
    from .fix import Fix
    from .repository import Repository
    from .user import User


class AnalysisStatus(str, enum.Enum):
    """Status of an analysis run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisRun(Base, UUIDPrimaryKeyMixin):
    """AnalysisRun model representing a single code health analysis job.

    Attributes:
        id: UUID primary key
        repository_id: Foreign key to the repository being analyzed
        commit_sha: Git commit SHA being analyzed
        branch: Git branch name
        status: Current status (queued, running, completed, failed)
        health_score: Calculated overall health score (0-100)
        structure_score: Structure category score (0-100)
        quality_score: Quality category score (0-100)
        architecture_score: Architecture category score (0-100)
        score_delta: Change in score from previous analysis (for PR analyses)
        findings_count: Number of issues found
        files_analyzed: Number of files analyzed
        progress_percent: Current progress percentage (0-100)
        current_step: Description of current analysis step
        triggered_by_id: User who triggered the analysis (optional)
        started_at: When the analysis started
        completed_at: When the analysis finished
        error_message: Error message if the analysis failed
        created_at: When the analysis was queued
        updated_at: When the record was last updated
        repository: The repository being analyzed
        triggered_by: The user who triggered the analysis
    """

    __tablename__ = "analysis_runs"

    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"),
        nullable=False,
    )
    commit_sha: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    branch: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    status: Mapped[AnalysisStatus] = mapped_column(
        Enum(
            AnalysisStatus,
            name="analysis_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=AnalysisStatus.QUEUED,
        nullable=False,
    )
    # Score fields
    health_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    structure_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    quality_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    architecture_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    score_delta: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    findings_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    files_analyzed: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    # Progress tracking fields
    progress_percent: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    current_step: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    # Trigger tracking
    triggered_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
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

    # Relationships
    repository: Mapped["Repository"] = relationship(
        "Repository",
        back_populates="analysis_runs",
    )
    triggered_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[triggered_by_id],
    )
    findings: Mapped[List["Finding"]] = relationship(
        "Finding",
        back_populates="analysis_run",
        cascade="all, delete-orphan",
    )
    fixes: Mapped[List["Fix"]] = relationship(
        "Fix",
        back_populates="analysis_run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_analysis_runs_repository_id", "repository_id"),
        Index("ix_analysis_runs_commit_sha", "commit_sha"),
        Index("ix_analysis_runs_status", "status"),
        Index("ix_analysis_runs_created_at", "created_at"),
        Index("ix_analysis_runs_triggered_by_id", "triggered_by_id"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "commit_sha", "status")

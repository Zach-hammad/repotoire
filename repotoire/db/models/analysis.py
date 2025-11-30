"""AnalysisRun model for tracking code analysis jobs.

This module defines the AnalysisRun model that tracks the status and
results of code health analysis runs for repositories.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .repository import Repository


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
        health_score: Calculated health score (0-100)
        findings_count: Number of issues found
        started_at: When the analysis started
        completed_at: When the analysis finished
        error_message: Error message if the analysis failed
        created_at: When the analysis was queued
        repository: The repository being analyzed
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
        Enum(AnalysisStatus, name="analysis_status"),
        default=AnalysisStatus.QUEUED,
        nullable=False,
    )
    health_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    findings_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
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

    # Relationships
    repository: Mapped["Repository"] = relationship(
        "Repository",
        back_populates="analysis_runs",
    )

    __table_args__ = (
        Index("ix_analysis_runs_repository_id", "repository_id"),
        Index("ix_analysis_runs_commit_sha", "commit_sha"),
        Index("ix_analysis_runs_status", "status"),
        Index("ix_analysis_runs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "commit_sha", "status")

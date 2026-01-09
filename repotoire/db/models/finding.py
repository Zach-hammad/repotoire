"""Finding model for storing analysis findings.

This module defines the Finding model that stores code health findings
detected during repository analysis, linked to AnalysisRun records.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .analysis import AnalysisRun
    from .fix import Fix


class FindingSeverity(str, enum.Enum):
    """Severity level of a finding."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingStatus(str, enum.Enum):
    """Status of a finding in the review workflow.

    Lifecycle:
        OPEN -> ACKNOWLEDGED -> IN_PROGRESS -> RESOLVED
        OPEN -> WONTFIX (intentionally not fixing)
        OPEN -> FALSE_POSITIVE (detector mistake)
        OPEN -> DUPLICATE (of another finding)
    """

    OPEN = "open"  # Newly detected, not yet reviewed
    ACKNOWLEDGED = "acknowledged"  # Team is aware, may address later
    IN_PROGRESS = "in_progress"  # Currently being worked on
    RESOLVED = "resolved"  # Issue has been fixed
    WONTFIX = "wontfix"  # Intentionally not fixing (acceptable tech debt)
    FALSE_POSITIVE = "false_positive"  # Not a real issue (detector mistake)
    DUPLICATE = "duplicate"  # Duplicate of another finding


class Finding(Base, UUIDPrimaryKeyMixin):
    """Finding model representing a code health issue detected during analysis.

    Attributes:
        id: UUID primary key
        analysis_run_id: Foreign key to the analysis run that found this issue
        detector: Name of the detector that found this issue
        severity: Severity level (critical, high, medium, low, info)
        status: Review status (open, acknowledged, resolved, wontfix, etc.)
        title: Short title describing the issue
        description: Detailed description with context
        affected_files: List of file paths affected
        affected_nodes: List of entity qualified names affected
        line_start: Starting line number where issue occurs
        line_end: Ending line number where issue occurs
        suggested_fix: Suggested fix for the issue
        estimated_effort: Estimated effort to fix (e.g., "Small (2-4 hours)")
        graph_context: Additional graph data about the issue (JSON)
        status_reason: Optional reason for status change (e.g., why it's a false positive)
        status_changed_by: User ID who changed the status
        status_changed_at: When the status was last changed
        created_at: When the finding was detected
        updated_at: When the finding was last updated
        analysis_run: The analysis run that found this issue
    """

    __tablename__ = "findings"

    analysis_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    detector: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    severity: Mapped[FindingSeverity] = mapped_column(
        Enum(
            FindingSeverity,
            name="finding_severity",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    status: Mapped[FindingStatus] = mapped_column(
        Enum(
            FindingStatus,
            name="finding_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=FindingStatus.OPEN,
        server_default="open",
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    affected_files: Mapped[List[str]] = mapped_column(
        ARRAY(String),
        default=list,
        nullable=False,
    )
    affected_nodes: Mapped[List[str]] = mapped_column(
        ARRAY(String),
        default=list,
        nullable=False,
    )
    line_start: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    line_end: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    suggested_fix: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    estimated_effort: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )
    graph_context: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )
    status_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Reason for status change (e.g., why marked as false positive)",
    )
    status_changed_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="User ID who last changed the status",
    )
    status_changed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When status was last changed",
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
    analysis_run: Mapped["AnalysisRun"] = relationship(
        "AnalysisRun",
        back_populates="findings",
    )
    fixes: Mapped[List["Fix"]] = relationship(
        "Fix",
        back_populates="finding",
    )

    __table_args__ = (
        Index("ix_findings_analysis_run_id", "analysis_run_id"),
        Index("ix_findings_severity", "severity"),
        Index("ix_findings_detector", "detector"),
        Index("ix_findings_status", "status"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "detector", "severity", "status", "title")

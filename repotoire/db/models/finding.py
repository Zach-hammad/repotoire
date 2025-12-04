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


class Finding(Base, UUIDPrimaryKeyMixin):
    """Finding model representing a code health issue detected during analysis.

    Attributes:
        id: UUID primary key
        analysis_run_id: Foreign key to the analysis run that found this issue
        detector: Name of the detector that found this issue
        severity: Severity level (critical, high, medium, low, info)
        title: Short title describing the issue
        description: Detailed description with context
        affected_files: List of file paths affected
        affected_nodes: List of entity qualified names affected
        line_start: Starting line number where issue occurs
        line_end: Ending line number where issue occurs
        suggested_fix: Suggested fix for the issue
        estimated_effort: Estimated effort to fix (e.g., "Small (2-4 hours)")
        graph_context: Additional graph data about the issue (JSON)
        created_at: When the finding was detected
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
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
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "detector", "severity", "title")

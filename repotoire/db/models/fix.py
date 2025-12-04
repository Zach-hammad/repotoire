"""Fix and FixComment models for storing auto-fix proposals and comments.

This module defines the Fix and FixComment models that store AI-generated
code fix proposals and reviewer comments, linked to AnalysisRun and Finding records.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional
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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .analysis import AnalysisRun
    from .finding import Finding
    from .user import User


class FixStatus(str, enum.Enum):
    """Status of a fix proposal."""

    PENDING = "pending"  # Awaiting human review
    APPROVED = "approved"  # Human approved
    REJECTED = "rejected"  # Human rejected
    APPLIED = "applied"  # Successfully applied
    FAILED = "failed"  # Failed to apply


class FixConfidence(str, enum.Enum):
    """Confidence level of auto-generated fix."""

    HIGH = "high"  # 90%+ confidence, safe to apply
    MEDIUM = "medium"  # 70-90% confidence, needs review
    LOW = "low"  # <70% confidence, careful review needed


class FixType(str, enum.Enum):
    """Type of fix being proposed."""

    REFACTOR = "refactor"  # Code restructuring
    SIMPLIFY = "simplify"  # Reduce complexity
    EXTRACT = "extract"  # Extract method/class
    RENAME = "rename"  # Rename for clarity
    REMOVE = "remove"  # Remove dead code
    SECURITY = "security"  # Fix security issue
    TYPE_HINT = "type_hint"  # Add type annotations
    DOCUMENTATION = "documentation"  # Add/fix docs


class Fix(Base, UUIDPrimaryKeyMixin):
    """Fix model representing an AI-generated code fix proposal.

    Attributes:
        id: UUID primary key
        analysis_run_id: Foreign key to the analysis run that generated this fix
        finding_id: Optional foreign key to the finding being fixed
        file_path: Path to the file being modified
        line_start: Starting line number of the change
        line_end: Ending line number of the change
        original_code: The original code being replaced
        fixed_code: The proposed fixed code
        title: Short title describing the fix
        description: Detailed description of the fix
        explanation: AI-generated explanation/rationale
        fix_type: Type of fix (refactor, simplify, etc.)
        confidence: Confidence level (high, medium, low)
        confidence_score: Numeric confidence score 0-1
        status: Current status (pending, approved, rejected, applied, failed)
        evidence: JSON containing similar patterns, docs, best practices
        validation_data: JSON containing syntax/import/type validation results
        created_at: When the fix was created
        updated_at: When the fix was last updated
        applied_at: When the fix was applied (if applied)
        analysis_run: The analysis run that generated this fix
        finding: The finding being fixed (optional)
        comments: Comments on this fix
    """

    __tablename__ = "fixes"

    # Relationships
    analysis_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    finding_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("findings.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Fix content
    file_path: Mapped[str] = mapped_column(
        String(1024),
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
    original_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    fixed_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Description
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    explanation: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Fix type and confidence
    fix_type: Mapped[FixType] = mapped_column(
        Enum(
            FixType,
            name="fix_type",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    confidence: Mapped[FixConfidence] = mapped_column(
        Enum(
            FixConfidence,
            name="fix_confidence",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )
    confidence_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    # Status
    status: Mapped[FixStatus] = mapped_column(
        Enum(
            FixStatus,
            name="fix_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=FixStatus.PENDING,
        nullable=False,
    )

    # Evidence and validation (JSON)
    evidence: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )
    validation_data: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )
    applied_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    analysis_run: Mapped["AnalysisRun"] = relationship(
        "AnalysisRun",
        back_populates="fixes",
    )
    finding: Mapped[Optional["Finding"]] = relationship(
        "Finding",
        back_populates="fixes",
    )
    comments: Mapped[List["FixComment"]] = relationship(
        "FixComment",
        back_populates="fix",
        cascade="all, delete-orphan",
        order_by="FixComment.created_at",
    )

    __table_args__ = (
        Index("ix_fixes_analysis_run_id", "analysis_run_id"),
        Index("ix_fixes_finding_id", "finding_id"),
        Index("ix_fixes_status", "status"),
        Index("ix_fixes_file_path", "file_path"),
        Index("ix_fixes_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "file_path", "status", "confidence")


class FixComment(Base, UUIDPrimaryKeyMixin):
    """FixComment model representing a comment on a fix proposal.

    Attributes:
        id: UUID primary key
        fix_id: Foreign key to the fix being commented on
        user_id: Foreign key to the user who created the comment
        content: The comment text
        created_at: When the comment was created
        fix: The fix being commented on
        user: The user who created the comment
    """

    __tablename__ = "fix_comments"

    fix_id: Mapped[UUID] = mapped_column(
        ForeignKey("fixes.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    fix: Mapped["Fix"] = relationship(
        "Fix",
        back_populates="comments",
    )
    user: Mapped["User"] = relationship(
        "User",
    )

    __table_args__ = (
        Index("ix_fix_comments_fix_id", "fix_id"),
        Index("ix_fix_comments_user_id", "user_id"),
        Index("ix_fix_comments_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "fix_id", "user_id")

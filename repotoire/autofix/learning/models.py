"""Data models for learning feedback system."""

from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field


class UserDecision(str, Enum):
    """User decision on a fix proposal."""

    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"  # User approved after making changes


class RejectionReason(str, Enum):
    """Reason for rejecting a fix."""

    STYLE_MISMATCH = "style_mismatch"  # Code style doesn't match project
    TOO_RISKY = "too_risky"  # Change is too risky or broad
    INCORRECT_LOGIC = "incorrect_logic"  # Fix logic is wrong
    NOT_NEEDED = "not_needed"  # The fix isn't necessary
    BREAKS_TESTS = "breaks_tests"  # Fix would break existing tests
    OTHER = "other"  # Other reason (see comment)


class FixDecision(BaseModel):
    """A recorded decision on a fix proposal."""

    id: str = Field(description="Unique decision ID")
    fix_id: str = Field(description="References the FixProposal.id")
    decision: UserDecision = Field(description="User's decision")
    rejection_reason: Optional[RejectionReason] = Field(
        default=None, description="Reason for rejection"
    )
    rejection_comment: Optional[str] = Field(
        default=None, description="User's comment on rejection"
    )

    # Context from the fix
    fix_type: str = Field(description="Type of fix (from FixProposal)")
    confidence: str = Field(description="Confidence level (HIGH/MEDIUM/LOW)")
    finding_type: str = Field(description="Type of issue being fixed")
    file_path: str = Field(description="Path to affected file")
    repository: str = Field(description="Repository path")

    # Timestamps
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Extensible metadata
    characteristics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible metadata (lines_changed, has_tests, etc.)",
    )

    def to_jsonl(self) -> str:
        """Serialize to JSONL format."""
        import json

        data = self.model_dump()
        data["timestamp"] = data["timestamp"].isoformat()
        data["decision"] = data["decision"].value if data["decision"] else None
        data["rejection_reason"] = (
            data["rejection_reason"].value if data["rejection_reason"] else None
        )
        return json.dumps(data)

    @classmethod
    def from_jsonl(cls, line: str) -> "FixDecision":
        """Deserialize from JSONL format."""
        import json

        data = json.loads(line)
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        if data.get("decision"):
            data["decision"] = UserDecision(data["decision"])
        if data.get("rejection_reason"):
            data["rejection_reason"] = RejectionReason(data["rejection_reason"])
        return cls(**data)


class RejectionPattern(BaseModel):
    """An identified pattern in rejections."""

    pattern: str = Field(description="Description of the pattern")
    rejection_rate: float = Field(description="Rejection rate (0-1)")
    sample_size: int = Field(description="Number of decisions analyzed")
    fix_type: Optional[str] = Field(default=None, description="Related fix type")
    reason: Optional[RejectionReason] = Field(
        default=None, description="Common rejection reason"
    )
    recommendation: str = Field(description="Recommendation based on pattern")
    user_comments: List[str] = Field(
        default_factory=list, description="Sample user comments"
    )


class LearningStats(BaseModel):
    """Statistics from learning feedback data."""

    total_decisions: int = Field(default=0, description="Total decisions recorded")
    approval_rate: float = Field(default=0.0, description="Overall approval rate")

    by_fix_type: Dict[str, Dict[str, int]] = Field(
        default_factory=dict,
        description='Breakdown by fix type (e.g., {"refactor": {"approved": 10, "rejected": 2}})',
    )
    by_confidence: Dict[str, Dict[str, int]] = Field(
        default_factory=dict,
        description='Breakdown by confidence level (e.g., {"HIGH": {"approved": 5}})',
    )
    by_finding_type: Dict[str, Dict[str, int]] = Field(
        default_factory=dict,
        description="Breakdown by finding type",
    )
    by_rejection_reason: Dict[str, int] = Field(
        default_factory=dict,
        description="Count by rejection reason",
    )

    rejection_patterns: List[RejectionPattern] = Field(
        default_factory=list,
        description="Identified patterns in rejections",
    )

    # Time-based stats
    recent_approval_rate: Optional[float] = Field(
        default=None, description="Approval rate in last 30 days"
    )
    trend: Optional[str] = Field(
        default=None, description="Trend direction (improving/declining/stable)"
    )

    def get_fix_type_approval_rate(self, fix_type: str) -> Optional[float]:
        """Get approval rate for a specific fix type."""
        stats = self.by_fix_type.get(fix_type, {})
        approved = stats.get("approved", 0)
        rejected = stats.get("rejected", 0)
        total = approved + rejected
        if total == 0:
            return None
        return approved / total

    def get_confidence_approval_rate(self, confidence: str) -> Optional[float]:
        """Get approval rate for a specific confidence level."""
        stats = self.by_confidence.get(confidence.upper(), {})
        approved = stats.get("approved", 0)
        rejected = stats.get("rejected", 0)
        total = approved + rejected
        if total == 0:
            return None
        return approved / total

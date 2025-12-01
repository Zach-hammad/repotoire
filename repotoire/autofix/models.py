"""Data models for auto-fix functionality."""

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from repotoire.models import Finding


class FixStatus(str, Enum):
    """Status of a fix proposal."""

    PENDING = "pending"  # Awaiting human review
    APPROVED = "approved"  # Human approved
    REJECTED = "rejected"  # Human rejected
    APPLIED = "applied"  # Successfully applied
    FAILED = "failed"  # Failed to apply


class FixConfidence(str, Enum):
    """Confidence level of auto-generated fix."""

    HIGH = "high"  # 90%+ confidence, safe to apply
    MEDIUM = "medium"  # 70-90% confidence, needs review
    LOW = "low"  # <70% confidence, careful review needed


class FixType(str, Enum):
    """Type of fix being proposed."""

    REFACTOR = "refactor"  # Code restructuring
    SIMPLIFY = "simplify"  # Reduce complexity
    EXTRACT = "extract"  # Extract method/class
    RENAME = "rename"  # Rename for clarity
    REMOVE = "remove"  # Remove dead code
    SECURITY = "security"  # Fix security issue
    TYPE_HINT = "type_hint"  # Add type annotations
    DOCUMENTATION = "documentation"  # Add/fix docs


class CodeChange(BaseModel):
    """A single code change within a fix."""

    file_path: Path
    original_code: str
    fixed_code: str
    start_line: int
    end_line: int
    description: str


class FixContext(BaseModel):
    """Context gathered for generating a fix."""

    finding: Finding
    related_code: List[str] = Field(
        default_factory=list, description="Related code snippets from RAG"
    )
    imports: List[str] = Field(default_factory=list, description="Relevant imports")
    dependencies: List[str] = Field(
        default_factory=list, description="Related functions/classes"
    )
    file_content: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    """Evidence supporting a fix proposal."""

    similar_patterns: List[str] = Field(
        default_factory=list,
        description="Code examples from codebase showing similar patterns"
    )
    documentation_refs: List[str] = Field(
        default_factory=list,
        description="References to documentation, PEPs, style guides"
    )
    best_practices: List[str] = Field(
        default_factory=list,
        description="Best practice justifications"
    )
    rag_context: List[str] = Field(
        default_factory=list,
        description="Related code snippets from RAG"
    )


class FixProposal(BaseModel):
    """A proposed fix for a code smell or issue."""

    id: str = Field(description="Unique fix ID")
    finding: Finding = Field(description="The finding being fixed")
    fix_type: FixType
    confidence: FixConfidence

    # Changes
    changes: List[CodeChange] = Field(description="Code changes to apply")

    # Description
    title: str = Field(description="Short fix title")
    description: str = Field(description="Detailed explanation of fix")
    rationale: str = Field(description="Why this fix addresses the issue")

    # Research backing
    evidence: Evidence = Field(
        default_factory=Evidence,
        description="Evidence and research supporting this fix"
    )

    # Metadata
    status: FixStatus = Field(default=FixStatus.PENDING)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    applied_at: Optional[datetime] = None

    # Validation (multi-level)
    syntax_valid: bool = Field(default=False, description="Level 1: AST syntax check")
    import_valid: Optional[bool] = Field(default=None, description="Level 2: Import check")
    type_valid: Optional[bool] = Field(default=None, description="Level 3: Type check")
    validation_errors: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Validation errors from all levels"
    )
    validation_warnings: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Non-blocking validation warnings"
    )

    # Tests
    tests_generated: bool = Field(default=False)
    test_code: Optional[str] = None

    # Git integration
    branch_name: Optional[str] = None
    commit_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "finding": (
                self.finding.to_dict()
                if hasattr(self.finding, "to_dict")
                else str(self.finding)
            ),
            "fix_type": self.fix_type.value,
            "confidence": self.confidence.value,
            "changes": [
                {
                    "file_path": str(c.file_path),
                    "original_code": c.original_code,
                    "fixed_code": c.fixed_code,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "description": c.description,
                }
                for c in self.changes
            ],
            "title": self.title,
            "description": self.description,
            "rationale": self.rationale,
            "evidence": {
                "similar_patterns": self.evidence.similar_patterns,
                "documentation_refs": self.evidence.documentation_refs,
                "best_practices": self.evidence.best_practices,
                "rag_context_count": len(self.evidence.rag_context),
            },
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "syntax_valid": self.syntax_valid,
            "import_valid": self.import_valid,
            "type_valid": self.type_valid,
            "validation_errors": self.validation_errors,
            "validation_warnings": self.validation_warnings,
            "tests_generated": self.tests_generated,
            "test_code": self.test_code,
            "branch_name": self.branch_name,
            "commit_message": self.commit_message,
        }


class FixBatch(BaseModel):
    """A batch of fixes for review."""

    fixes: List[FixProposal]
    total_findings: int
    fixable_count: int
    unfixable_count: int
    high_confidence_count: int
    created_at: datetime = Field(default_factory=datetime.utcnow)

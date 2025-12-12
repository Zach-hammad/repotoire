"""Pydantic schemas for Fix API endpoints."""

from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from repotoire.db.models.fix import FixConfidence, FixStatus, FixType


class CodeChangeSchema(BaseModel):
    """Schema for a single code change within a fix."""

    file_path: str = Field(..., description="Path to the file being modified")
    original_code: str = Field(..., description="Original code being replaced")
    fixed_code: str = Field(..., description="Proposed fixed code")
    start_line: int = Field(..., description="Starting line number of the change", ge=1)
    end_line: int = Field(..., description="Ending line number of the change", ge=1)
    description: str = Field(..., description="Description of this specific change")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "file_path": "src/utils.py",
                "original_code": "def calculate(x):\n    return x * 2",
                "fixed_code": "def calculate(x: int) -> int:\n    return x * 2",
                "start_line": 10,
                "end_line": 11,
                "description": "Add type hints to calculate function",
            }
        }
    )


class EvidenceSchema(BaseModel):
    """Schema for evidence supporting a fix proposal."""

    similar_patterns: List[str] = Field(
        default_factory=list,
        description="Code examples from codebase showing similar patterns",
    )
    documentation_refs: List[str] = Field(
        default_factory=list,
        description="References to documentation, PEPs, style guides",
    )
    best_practices: List[str] = Field(
        default_factory=list,
        description="Best practice justifications",
    )
    rag_context_count: int = Field(
        default=0,
        description="Number of RAG context snippets used",
    )


class ValidationDataSchema(BaseModel):
    """Schema for validation results."""

    syntax_valid: bool = Field(default=False, description="AST syntax check passed")
    import_valid: Optional[bool] = Field(default=None, description="Import check passed")
    type_valid: Optional[bool] = Field(default=None, description="Type check passed")
    errors: List[dict] = Field(default_factory=list, description="Validation errors")
    warnings: List[dict] = Field(default_factory=list, description="Validation warnings")


class FixCreate(BaseModel):
    """Schema for creating a new fix."""

    analysis_run_id: UUID = Field(..., description="ID of the analysis run")
    finding_id: Optional[UUID] = Field(None, description="ID of the finding being fixed")
    file_path: str = Field(..., description="Path to the file being modified")
    line_start: Optional[int] = Field(None, description="Starting line number", ge=1)
    line_end: Optional[int] = Field(None, description="Ending line number", ge=1)
    original_code: str = Field(..., description="Original code being replaced")
    fixed_code: str = Field(..., description="Proposed fixed code")
    title: str = Field(..., description="Short title for the fix", max_length=500)
    description: str = Field(..., description="Detailed description of the fix")
    explanation: str = Field(..., description="AI-generated rationale")
    fix_type: FixType = Field(..., description="Type of fix")
    confidence: FixConfidence = Field(..., description="Confidence level")
    confidence_score: float = Field(..., description="Numeric confidence (0-1)", ge=0, le=1)
    evidence: Optional[dict] = Field(None, description="Evidence supporting the fix")
    validation_data: Optional[dict] = Field(None, description="Validation results")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "analysis_run_id": "123e4567-e89b-12d3-a456-426614174000",
                "finding_id": "123e4567-e89b-12d3-a456-426614174001",
                "file_path": "src/utils.py",
                "line_start": 10,
                "line_end": 15,
                "original_code": "def calculate(x):\n    return x * 2",
                "fixed_code": "def calculate(x: int) -> int:\n    return x * 2",
                "title": "Add type hints to calculate function",
                "description": "This fix adds type annotations to improve code clarity.",
                "explanation": "Type hints improve IDE support and catch errors early.",
                "fix_type": "type_hint",
                "confidence": "high",
                "confidence_score": 0.95,
            }
        }
    )


class FixUpdate(BaseModel):
    """Schema for updating an existing fix."""

    title: Optional[str] = Field(None, description="New title", max_length=500)
    description: Optional[str] = Field(None, description="New description")
    fixed_code: Optional[str] = Field(None, description="Updated fixed code")
    validation_data: Optional[dict] = Field(None, description="Updated validation results")


class UpdateFixStatusRequest(BaseModel):
    """Schema for updating fix status."""

    status: FixStatus = Field(..., description="New status for the fix")
    reason: Optional[str] = Field(
        None,
        description="Reason for status change (required for rejection)",
        max_length=1000,
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "approved",
                "reason": None,
            }
        }
    )


class CommentCreate(BaseModel):
    """Schema for creating a comment on a fix."""

    content: str = Field(
        ...,
        description="Comment text",
        min_length=1,
        max_length=10000,
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "Looks good, but consider adding a docstring.",
            }
        }
    )


class FixCommentResponse(BaseModel):
    """Schema for a fix comment response."""

    id: UUID = Field(..., description="Comment ID")
    fix_id: UUID = Field(..., description="ID of the fix this comment belongs to")
    user_id: UUID = Field(..., description="ID of the user who created the comment")
    user_name: Optional[str] = Field(None, description="Name of the user")
    user_email: Optional[str] = Field(None, description="Email of the user")
    content: str = Field(..., description="Comment text")
    created_at: datetime = Field(..., description="When the comment was created")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174002",
                "fix_id": "123e4567-e89b-12d3-a456-426614174001",
                "user_id": "123e4567-e89b-12d3-a456-426614174000",
                "user_name": "John Doe",
                "user_email": "john@example.com",
                "content": "Looks good!",
                "created_at": "2025-12-04T12:00:00Z",
            }
        },
    )


class FixResponse(BaseModel):
    """Schema for a fix response."""

    id: UUID = Field(..., description="Fix ID")
    analysis_run_id: UUID = Field(..., description="ID of the analysis run")
    finding_id: Optional[UUID] = Field(None, description="ID of the finding being fixed")
    file_path: str = Field(..., description="Path to the file being modified")
    line_start: Optional[int] = Field(None, description="Starting line number")
    line_end: Optional[int] = Field(None, description="Ending line number")
    original_code: str = Field(..., description="Original code")
    fixed_code: str = Field(..., description="Proposed fixed code")
    title: str = Field(..., description="Short title")
    description: str = Field(..., description="Detailed description")
    explanation: str = Field(..., description="AI-generated rationale")
    fix_type: FixType = Field(..., description="Type of fix")
    confidence: FixConfidence = Field(..., description="Confidence level")
    confidence_score: float = Field(..., description="Numeric confidence (0-1)")
    status: FixStatus = Field(..., description="Current status")
    evidence: Optional[dict] = Field(None, description="Evidence supporting the fix")
    validation_data: Optional[dict] = Field(None, description="Validation results")
    created_at: datetime = Field(..., description="When the fix was created")
    updated_at: Optional[datetime] = Field(None, description="When the fix was last updated")
    applied_at: Optional[datetime] = Field(None, description="When the fix was applied")
    comments: List[FixCommentResponse] = Field(
        default_factory=list,
        description="Comments on this fix",
    )
    comments_count: int = Field(default=0, description="Number of comments")

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "123e4567-e89b-12d3-a456-426614174001",
                "analysis_run_id": "123e4567-e89b-12d3-a456-426614174000",
                "finding_id": None,
                "file_path": "src/utils.py",
                "line_start": 10,
                "line_end": 15,
                "original_code": "def calculate(x):\n    return x * 2",
                "fixed_code": "def calculate(x: int) -> int:\n    return x * 2",
                "title": "Add type hints to calculate function",
                "description": "This fix adds type annotations.",
                "explanation": "Type hints improve IDE support.",
                "fix_type": "type_hint",
                "confidence": "high",
                "confidence_score": 0.95,
                "status": "pending",
                "evidence": None,
                "validation_data": None,
                "created_at": "2025-12-04T12:00:00Z",
                "updated_at": None,
                "applied_at": None,
                "comments": [],
                "comments_count": 0,
            }
        },
    )

    @classmethod
    def from_db_model(cls, fix: Any, include_comments: bool = False) -> "FixResponse":
        """Create a FixResponse from a database model.

        Args:
            fix: The Fix database model instance
            include_comments: Whether to include comments in the response

        Returns:
            FixResponse instance
        """
        comments = []
        if include_comments and hasattr(fix, "comments"):
            comments = [
                FixCommentResponse(
                    id=c.id,
                    fix_id=c.fix_id,
                    user_id=c.user_id,
                    user_name=c.user.name if hasattr(c, "user") and c.user else None,
                    user_email=c.user.email if hasattr(c, "user") and c.user else None,
                    content=c.content,
                    created_at=c.created_at,
                )
                for c in fix.comments
            ]

        return cls(
            id=fix.id,
            analysis_run_id=fix.analysis_run_id,
            finding_id=fix.finding_id,
            file_path=fix.file_path,
            line_start=fix.line_start,
            line_end=fix.line_end,
            original_code=fix.original_code,
            fixed_code=fix.fixed_code,
            title=fix.title,
            description=fix.description,
            explanation=fix.explanation,
            fix_type=fix.fix_type,
            confidence=fix.confidence,
            confidence_score=fix.confidence_score,
            status=fix.status,
            evidence=fix.evidence,
            validation_data=fix.validation_data,
            created_at=fix.created_at,
            updated_at=fix.updated_at,
            applied_at=fix.applied_at,
            comments=comments,
            comments_count=len(fix.comments) if hasattr(fix, "comments") else 0,
        )


class FixListResponse(BaseModel):
    """Schema for a list of fixes response."""

    fixes: List[FixResponse] = Field(..., description="List of fixes")
    total: int = Field(..., description="Total number of fixes matching the query")


class PaginatedResponse(BaseModel):
    """Schema for paginated response."""

    items: List[FixResponse] = Field(..., description="List of items")
    total: int = Field(..., description="Total number of items")
    page: int = Field(..., description="Current page number", ge=1)
    page_size: int = Field(..., description="Number of items per page", ge=1)
    has_more: bool = Field(..., description="Whether there are more pages")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [],
                "total": 100,
                "page": 1,
                "page_size": 20,
                "has_more": True,
            }
        }
    )


class BatchRequest(BaseModel):
    """Schema for batch operations."""

    ids: List[UUID] = Field(..., description="List of fix IDs")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ids": [
                    "123e4567-e89b-12d3-a456-426614174001",
                    "123e4567-e89b-12d3-a456-426614174002",
                ],
            }
        }
    )


class BatchRejectRequest(BatchRequest):
    """Schema for batch reject operation."""

    reason: str = Field(
        ...,
        description="Reason for rejecting the fixes",
        min_length=1,
        max_length=1000,
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ids": [
                    "123e4567-e89b-12d3-a456-426614174001",
                    "123e4567-e89b-12d3-a456-426614174002",
                ],
                "reason": "These fixes don't match our coding standards.",
            }
        }
    )


class BatchOperationResult(BaseModel):
    """Schema for batch operation result."""

    success: bool = Field(..., description="Whether the operation succeeded")
    processed: int = Field(..., description="Number of items processed")
    failed: int = Field(default=0, description="Number of items that failed")
    errors: List[str] = Field(default_factory=list, description="Error messages for failures")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "processed": 5,
                "failed": 0,
                "errors": [],
            }
        }
    )

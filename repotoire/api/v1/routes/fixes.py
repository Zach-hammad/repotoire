"""API routes for fix management and Best-of-N generation."""

from __future__ import annotations

import ast
import time
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.autofix.models import (
    FixProposal,
    FixStatus as AutofixFixStatus,
    FixConfidence as AutofixFixConfidence,
    FixType as AutofixFixType,
)
from repotoire.autofix.entitlements import (
    FeatureAccess,
    get_customer_entitlement,
)
from repotoire.autofix.best_of_n import (
    BestOfNConfig,
    BestOfNGenerator,
    BestOfNNotAvailableError,
    BestOfNUsageLimitError,
)
from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.api.shared.middleware.usage import enforce_feature
from repotoire.api.models import PreviewResult, PreviewCheck
from repotoire.db.models import Organization, PlanTier
from repotoire.db.models.fix import Fix, FixStatus, FixConfidence, FixType
from repotoire.db.models.user import User
from repotoire.db.repositories.fix import FixRepository
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.detectors.health_delta import (
    HealthScoreDeltaCalculator,
    HealthScoreDelta,
    BatchHealthScoreDelta,
    ImpactLevel,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    from repotoire.cache import PreviewCache

logger = get_logger(__name__)

router = APIRouter(prefix="/fixes", tags=["fixes"])

# In-memory storage for legacy FixProposal objects (Best-of-N)
_fixes_store: dict[str, FixProposal] = {}
_comments_store: dict[str, list] = {}


def _db_fix_to_preview_proposal(fix: Fix) -> FixProposal:
    """Convert a database Fix model to a FixProposal for preview validation.

    This allows the preview endpoint to work with fixes stored in the database,
    not just the in-memory _fixes_store used by Best-of-N generation.
    """
    from repotoire.autofix.models import CodeChange, Evidence, Finding as AutofixFinding
    from repotoire.models import Severity

    # Create a minimal finding for the proposal
    finding = AutofixFinding(
        id=str(fix.finding_id) if fix.finding_id else f"finding-{fix.id}",
        title=fix.title,
        description=fix.description or "",
        severity=Severity.MEDIUM,  # Default severity
        detector="unknown",
        affected_nodes=[],
        affected_files=[fix.file_path] if fix.file_path else [],
    )

    # Create the code change
    change = CodeChange(
        file_path=fix.file_path or "unknown.py",
        original_code=fix.original_code or "",
        fixed_code=fix.fixed_code or "",
        start_line=fix.line_start or 0,
        end_line=fix.line_end or 0,
        description=fix.description or "",
    )

    # Create evidence from stored data
    evidence_data = fix.evidence or {}
    evidence = Evidence(
        similar_patterns=evidence_data.get("similar_patterns", []),
        documentation_refs=evidence_data.get("documentation_refs", []),
        best_practices=evidence_data.get("best_practices", []),
        rag_context_count=evidence_data.get("rag_context_count", 0),
    )

    # Map database enum values to autofix enum values
    confidence_map = {
        FixConfidence.HIGH: AutofixFixConfidence.HIGH,
        FixConfidence.MEDIUM: AutofixFixConfidence.MEDIUM,
        FixConfidence.LOW: AutofixFixConfidence.LOW,
    }
    fix_type_map = {
        FixType.REFACTOR: AutofixFixType.REFACTOR,
        FixType.SIMPLIFY: AutofixFixType.SIMPLIFY,
        FixType.EXTRACT: AutofixFixType.EXTRACT,
        FixType.RENAME: AutofixFixType.RENAME,
        FixType.REMOVE: AutofixFixType.REMOVE,
        FixType.SECURITY: AutofixFixType.SECURITY,
        FixType.TYPE_HINT: AutofixFixType.TYPE_HINT,
        FixType.DOCUMENTATION: AutofixFixType.DOCUMENTATION,
    }
    status_map = {
        FixStatus.PENDING: AutofixFixStatus.PENDING,
        FixStatus.APPROVED: AutofixFixStatus.APPROVED,
        FixStatus.REJECTED: AutofixFixStatus.REJECTED,
        FixStatus.APPLIED: AutofixFixStatus.APPLIED,
        FixStatus.FAILED: AutofixFixStatus.FAILED,
    }

    return FixProposal(
        id=str(fix.id),
        finding=finding,
        fix_type=fix_type_map.get(fix.fix_type, AutofixFixType.REFACTOR),
        confidence=confidence_map.get(fix.confidence, AutofixFixConfidence.MEDIUM),
        changes=[change],
        title=fix.title,
        description=fix.description or "",
        rationale=fix.explanation or "",
        evidence=evidence,
        status=status_map.get(fix.status, AutofixFixStatus.PENDING),
        created_at=fix.created_at or datetime.utcnow(),
        applied_at=fix.applied_at,
        syntax_valid=fix.validation_data.get("syntax_valid", True) if fix.validation_data else True,
        import_valid=fix.validation_data.get("import_valid") if fix.validation_data else None,
        type_valid=fix.validation_data.get("type_valid") if fix.validation_data else None,
    )


async def _get_db_user(db: AsyncSession, clerk_user_id: str) -> Optional[User]:
    """Get database user by Clerk user ID."""
    result = await db.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    return result.scalar_one_or_none()


def _fix_to_dict(fix: Fix) -> dict:
    """Convert a Fix DB model to API response dict."""
    # Ensure evidence has the expected structure
    evidence = fix.evidence or {}
    evidence_structured = {
        "similar_patterns": evidence.get("similar_patterns", []),
        "documentation_refs": evidence.get("documentation_refs", []),
        "best_practices": evidence.get("best_practices", []),
        "rag_context_count": evidence.get("rag_context_count", 0),
    }

    return {
        "id": str(fix.id),
        "finding_id": str(fix.finding_id) if fix.finding_id else None,
        "finding": {"id": str(fix.finding_id)} if fix.finding_id else None,
        "fix_type": fix.fix_type.value,
        "confidence": fix.confidence.value,
        "changes": [{
            "file_path": fix.file_path,
            "original_code": fix.original_code,
            "fixed_code": fix.fixed_code,
            "start_line": fix.line_start or 0,
            "end_line": fix.line_end or 0,
            "description": fix.description,
        }],
        "title": fix.title,
        "description": fix.description,
        "rationale": fix.explanation,
        "evidence": evidence_structured,
        "status": fix.status.value,
        "created_at": fix.created_at.isoformat() if fix.created_at else None,
        "applied_at": fix.applied_at.isoformat() if fix.applied_at else None,
        "syntax_valid": fix.validation_data.get("syntax_valid", True) if fix.validation_data else True,
        "import_valid": fix.validation_data.get("import_valid") if fix.validation_data else None,
        "type_valid": fix.validation_data.get("type_valid") if fix.validation_data else None,
        "validation_errors": fix.validation_data.get("errors", []) if fix.validation_data else [],
        "validation_warnings": fix.validation_data.get("warnings", []) if fix.validation_data else [],
        "tests_generated": False,
        "test_code": None,
        "branch_name": None,
        "commit_message": None,
    }


class PaginatedResponse(BaseModel):
    """Paginated response wrapper."""

    items: List[dict] = Field(..., description="List of fix objects")
    total: int = Field(..., description="Total number of fixes matching filters", ge=0)
    page: int = Field(..., description="Current page number (1-indexed)", ge=1)
    page_size: int = Field(..., description="Items per page", ge=1, le=100)
    has_more: bool = Field(..., description="Whether more pages are available")

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "finding_id": "660e8400-e29b-41d4-a716-446655440001",
                        "fix_type": "code_change",
                        "confidence": "high",
                        "title": "Fix hardcoded password",
                        "status": "pending",
                    }
                ],
                "total": 15,
                "page": 1,
                "page_size": 20,
                "has_more": False,
            }
        }
    }


class FixComment(BaseModel):
    """A comment on a fix."""

    id: str = Field(..., description="Unique comment identifier")
    fix_id: str = Field(..., description="ID of the fix this comment belongs to")
    author: str = Field(..., description="Author's user ID or email")
    content: str = Field(..., description="Comment content")
    created_at: datetime = Field(..., description="When the comment was created")


class CommentCreate(BaseModel):
    """Request to create a comment on a fix."""

    content: str = Field(
        ...,
        description="Comment text",
        min_length=1,
        max_length=5000,
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "content": "This fix looks good, but consider also updating the related config file."
            }
        }
    }


class RejectRequest(BaseModel):
    """Request to reject a fix."""

    reason: str = Field(
        ...,
        description="Reason for rejecting the fix",
        min_length=1,
        max_length=1000,
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "reason": "This change breaks backward compatibility. Need a migration path."
            }
        }
    }


class BatchRequest(BaseModel):
    """Request for batch operations."""

    ids: List[str] = Field(
        ...,
        description="List of fix IDs to operate on",
        min_length=1,
        max_length=100,
    )


class BatchRejectRequest(BatchRequest):
    """Request for batch reject."""

    reason: str = Field(
        ...,
        description="Reason for rejecting all selected fixes",
        min_length=1,
        max_length=1000,
    )


class ApplyFixRequest(BaseModel):
    """Request to apply a fix to the repository."""

    repository_path: str = Field(
        ...,
        description="Absolute path to the repository where the fix should be applied",
        json_schema_extra={"example": "/home/user/projects/my-app"},
    )
    create_branch: bool = Field(
        default=True,
        description="Create a new git branch for the fix (recommended for review)",
    )
    commit: bool = Field(
        default=True,
        description="Create a git commit with the fix",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "repository_path": "/home/user/projects/my-app",
                "create_branch": True,
                "commit": True,
            }
        }
    }


@router.get(
    "",
    response_model=PaginatedResponse,
    summary="List fixes",
    description="""
List AI-generated fix proposals with filtering and pagination.

**Fix Statuses:**
- `pending` - Awaiting review
- `approved` - Approved by reviewer
- `rejected` - Rejected by reviewer
- `applied` - Successfully applied to codebase
- `failed` - Failed to apply

**Confidence Levels:**
- `high` - High confidence fix, likely correct
- `medium` - Moderate confidence, needs review
- `low` - Low confidence, manual review recommended

**Fix Types:**
- `code_change` - Direct code modification
- `configuration` - Configuration file change
- `dependency` - Dependency update
    """,
    responses={
        200: {"description": "Fixes retrieved successfully"},
    },
)
async def list_fixes(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: Optional[List[str]] = Query(None, description="Filter by status (pending, approved, rejected, applied, failed, stale)"),
    confidence: Optional[List[str]] = Query(None, description="Filter by confidence (high, medium, low)"),
    fix_type: Optional[List[str]] = Query(None, description="Filter by fix type"),
    repository_id: Optional[str] = Query(None, description="Filter by repository UUID"),
    search: Optional[str] = Query(None, description="Search in title and description"),
    sort_by: str = Query("created_at", description="Field to sort by"),
    sort_direction: str = Query("desc", description="Sort direction: 'asc' or 'desc'"),
    include_stale: bool = Query(False, description="Include stale fixes (code has changed since fix was generated)"),
) -> PaginatedResponse:
    """List AI-generated fix proposals with filtering and pagination."""
    repo = FixRepository(db)

    # Convert string params to enums
    status_enums = [FixStatus(s) for s in status] if status else None
    confidence_enums = [FixConfidence(c) for c in confidence] if confidence else None
    fix_type_enums = [FixType(t) for t in fix_type] if fix_type else None
    repo_uuid = UUID(repository_id) if repository_id else None

    # Exclude stale fixes by default unless explicitly requested or filtering by status
    exclude_status = None
    if not include_stale and status is None:
        exclude_status = [FixStatus.STALE]

    # Calculate offset
    offset = (page - 1) * page_size

    # Get fixes from database
    fixes, total = await repo.search(
        repository_id=repo_uuid,
        status=status_enums,
        confidence=confidence_enums,
        fix_type=fix_type_enums,
        search_text=search,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=page_size,
        offset=offset,
        exclude_status=exclude_status,
    )

    return PaginatedResponse(
        items=[_fix_to_dict(f) for f in fixes],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + page_size) < total,
    )


@router.get(
    "/{fix_id}",
    summary="Get fix details",
    description="""
Get detailed information about a specific fix proposal.

Returns the full fix object including:
- Original and fixed code
- Explanation and rationale
- Evidence from RAG context (similar patterns, documentation refs)
- Validation status (syntax, imports, types)
- Current status and timestamps
    """,
    responses={
        200: {"description": "Fix retrieved successfully"},
        400: {
            "description": "Invalid fix ID format",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid fix ID format"}
                }
            },
        },
        404: {
            "description": "Fix not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Fix not found"}
                }
            },
        },
    },
)
async def get_fix(
    fix_id: str,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get detailed information about a specific fix proposal."""
    repo = FixRepository(db)
    try:
        fix = await repo.get_by_id(UUID(fix_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fix ID format")

    if fix is None:
        raise HTTPException(status_code=404, detail="Fix not found")
    return _fix_to_dict(fix)


class HealthScoreDeltaResponse(BaseModel):
    """Response with health score delta estimation."""

    before_score: float = Field(..., description="Current health score")
    after_score: float = Field(..., description="Projected score after fix")
    score_delta: float = Field(..., description="Points improvement (positive = better)")
    before_grade: str = Field(..., description="Current letter grade (A-F)")
    after_grade: str = Field(..., description="Projected letter grade after fix")
    grade_improved: bool = Field(..., description="Whether grade would improve")
    grade_change: Optional[str] = Field(None, description="Grade change string (e.g., 'B → A')")
    structure_delta: float = Field(..., description="Points change in structure category")
    quality_delta: float = Field(..., description="Points change in quality category")
    architecture_delta: float = Field(..., description="Points change in architecture category")
    impact_level: str = Field(..., description="Impact classification: critical, high, medium, low, negligible")
    affected_metric: str = Field(..., description="Which metric would be improved")
    finding_id: Optional[str] = Field(None, description="ID of the related finding")
    finding_severity: Optional[str] = Field(None, description="Severity of the finding")


class BatchHealthScoreDeltaRequest(BaseModel):
    """Request for batch impact estimation."""

    ids: List[str] = Field(
        ...,
        description="List of fix IDs to estimate impact for",
        min_length=1,
        max_length=50,
    )


class BatchHealthScoreDeltaResponse(BaseModel):
    """Response with batch health score delta estimation."""

    before_score: float
    after_score: float
    score_delta: float
    before_grade: str
    after_grade: str
    grade_improved: bool
    grade_change: Optional[str] = None
    findings_count: int
    individual_deltas: List[HealthScoreDeltaResponse] = []


@router.post(
    "/{fix_id}/estimate-impact",
    response_model=HealthScoreDeltaResponse,
    summary="Estimate fix health impact",
    description="""
Estimate how applying this fix would impact the codebase health score.

**What This Shows:**
- Before/after health score comparison
- Grade change (A-F) if applicable
- Category-level improvements (Structure, Quality, Architecture)
- Impact classification (critical, high, medium, low, negligible)

**Impact Levels:**
- `critical`: >5 points improvement or grade change
- `high`: 2-5 points improvement
- `medium`: 0.5-2 points improvement
- `low`: <0.5 points improvement
- `negligible`: <0.1 points improvement

**Note:** This is an estimation based on the fix's detector type and the
current codebase metrics. Actual impact may vary depending on codebase state.
    """,
    responses={
        200: {"description": "Impact estimation successful"},
        400: {"description": "Invalid fix ID format"},
        404: {"description": "Fix not found"},
        503: {"description": "Metrics not available for estimation"},
    },
)
async def estimate_fix_impact(
    fix_id: str,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HealthScoreDeltaResponse:
    """Estimate how applying this fix would impact the health score."""
    from repotoire.db.models import AnalysisRun, Repository
    from repotoire.models import Finding, Severity, MetricsBreakdown

    repo = FixRepository(db)
    try:
        fix_uuid = UUID(fix_id)
        fix = await repo.get_by_id(fix_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fix ID format")

    if fix is None:
        raise HTTPException(status_code=404, detail="Fix not found")

    # Get repository and latest analysis metrics
    if fix.repository_id:
        from sqlalchemy import desc

        result = await db.execute(
            select(AnalysisRun)
            .where(AnalysisRun.repository_id == fix.repository_id)
            .order_by(desc(AnalysisRun.created_at))
            .limit(1)
        )
        latest_analysis = result.scalar_one_or_none()

        if latest_analysis and latest_analysis.metrics:
            metrics_data = latest_analysis.metrics
        else:
            # Use default metrics if no analysis available
            metrics_data = {}
    else:
        metrics_data = {}

    # Build MetricsBreakdown from stored metrics or defaults
    metrics = MetricsBreakdown(
        total_files=metrics_data.get("total_files", 100),
        total_classes=metrics_data.get("total_classes", 50),
        total_functions=metrics_data.get("total_functions", 200),
        modularity=metrics_data.get("modularity", 0.7),
        avg_coupling=metrics_data.get("avg_coupling", 3.0),
        circular_dependencies=metrics_data.get("circular_dependencies", 0),
        bottleneck_count=metrics_data.get("bottleneck_count", 2),
        dead_code_percentage=metrics_data.get("dead_code_percentage", 0.05),
        duplication_percentage=metrics_data.get("duplication_percentage", 0.03),
        god_class_count=metrics_data.get("god_class_count", 1),
        layer_violations=metrics_data.get("layer_violations", 0),
        boundary_violations=metrics_data.get("boundary_violations", 0),
        abstraction_ratio=metrics_data.get("abstraction_ratio", 0.5),
    )

    # Create a Finding object from the fix data
    # Map fix type to a detector name for delta calculation
    detector_mapping = {
        FixType.REFACTOR: "GodClassDetector",
        FixType.SIMPLIFY: "GodClassDetector",
        FixType.EXTRACT: "FeatureEnvyDetector",
        FixType.RENAME: "MiddleManDetector",
        FixType.REMOVE: "DeadCodeDetector",
        FixType.SECURITY: "BanditDetector",
        FixType.TYPE_HINT: "MypyDetector",
        FixType.DOCUMENTATION: "PylintDetector",
    }

    detector = detector_mapping.get(fix.fix_type, "GodClassDetector")

    finding = Finding(
        id=str(fix.finding_id) if fix.finding_id else str(fix.id),
        title=fix.title,
        description=fix.description,
        severity=Severity.MEDIUM,  # Default, could be extracted from finding if available
        detector=detector,
        affected_files=[fix.file_path],
    )

    # Calculate delta
    calculator = HealthScoreDeltaCalculator()
    delta = calculator.calculate_delta(metrics, finding)

    return HealthScoreDeltaResponse(
        before_score=round(delta.before_score, 1),
        after_score=round(delta.after_score, 1),
        score_delta=round(delta.score_delta, 2),
        before_grade=delta.before_grade,
        after_grade=delta.after_grade,
        grade_improved=delta.grade_improved,
        grade_change=delta.grade_change_str,
        structure_delta=round(delta.structure_delta, 2),
        quality_delta=round(delta.quality_delta, 2),
        architecture_delta=round(delta.architecture_delta, 2),
        impact_level=delta.impact_level.value,
        affected_metric=delta.affected_metric,
        finding_id=str(fix.finding_id) if fix.finding_id else None,
        finding_severity=finding.severity.value,
    )


@router.post(
    "/batch/estimate-impact",
    response_model=BatchHealthScoreDeltaResponse,
    summary="Estimate batch fix health impact",
    description="""
Estimate the aggregate health score impact of applying multiple fixes.

Shows both:
- Total aggregate impact if all fixes are applied
- Individual impact of each fix
    """,
    responses={
        200: {"description": "Batch impact estimation successful"},
        400: {"description": "Invalid fix ID format"},
    },
)
async def estimate_batch_fix_impact(
    request: BatchHealthScoreDeltaRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BatchHealthScoreDeltaResponse:
    """Estimate aggregate impact of applying multiple fixes."""
    from repotoire.models import Finding, Severity, MetricsBreakdown

    repo = FixRepository(db)
    individual_deltas: List[HealthScoreDeltaResponse] = []
    findings: List[Finding] = []

    # Get default metrics (would be fetched from latest analysis in production)
    metrics = MetricsBreakdown(
        total_files=100,
        total_classes=50,
        total_functions=200,
        modularity=0.7,
        avg_coupling=3.0,
        circular_dependencies=0,
        bottleneck_count=2,
        dead_code_percentage=0.05,
        duplication_percentage=0.03,
        god_class_count=1,
        layer_violations=0,
        boundary_violations=0,
        abstraction_ratio=0.5,
    )

    detector_mapping = {
        FixType.REFACTOR: "GodClassDetector",
        FixType.SIMPLIFY: "GodClassDetector",
        FixType.EXTRACT: "FeatureEnvyDetector",
        FixType.RENAME: "MiddleManDetector",
        FixType.REMOVE: "DeadCodeDetector",
        FixType.SECURITY: "BanditDetector",
        FixType.TYPE_HINT: "MypyDetector",
        FixType.DOCUMENTATION: "PylintDetector",
    }

    calculator = HealthScoreDeltaCalculator()

    for fix_id in request.ids:
        try:
            fix_uuid = UUID(fix_id)
            fix = await repo.get_by_id(fix_uuid)
        except ValueError:
            continue

        if fix is None:
            continue

        detector = detector_mapping.get(fix.fix_type, "GodClassDetector")

        finding = Finding(
            id=str(fix.finding_id) if fix.finding_id else str(fix.id),
            title=fix.title,
            description=fix.description,
            severity=Severity.MEDIUM,
            detector=detector,
            affected_files=[fix.file_path],
        )
        findings.append(finding)

        # Calculate individual delta
        delta = calculator.calculate_delta(metrics, finding)
        individual_deltas.append(HealthScoreDeltaResponse(
            before_score=round(delta.before_score, 1),
            after_score=round(delta.after_score, 1),
            score_delta=round(delta.score_delta, 2),
            before_grade=delta.before_grade,
            after_grade=delta.after_grade,
            grade_improved=delta.grade_improved,
            grade_change=delta.grade_change_str,
            structure_delta=round(delta.structure_delta, 2),
            quality_delta=round(delta.quality_delta, 2),
            architecture_delta=round(delta.architecture_delta, 2),
            impact_level=delta.impact_level.value,
            affected_metric=delta.affected_metric,
            finding_id=str(fix.finding_id) if fix.finding_id else None,
            finding_severity=finding.severity.value,
        ))

    # Calculate batch delta
    batch_delta = calculator.calculate_batch_delta(metrics, findings)

    return BatchHealthScoreDeltaResponse(
        before_score=round(batch_delta.before_score, 1),
        after_score=round(batch_delta.after_score, 1),
        score_delta=round(batch_delta.score_delta, 2),
        before_grade=batch_delta.before_grade,
        after_grade=batch_delta.after_grade,
        grade_improved=batch_delta.grade_improved,
        grade_change=f"{batch_delta.before_grade} → {batch_delta.after_grade}" if batch_delta.grade_improved else None,
        findings_count=batch_delta.findings_count,
        individual_deltas=individual_deltas,
    )


@router.post(
    "/{fix_id}/approve",
    summary="Approve fix",
    description="""
Approve a fix proposal for application.

Marks the fix as approved so it can be applied to the codebase.
Only fixes with status `pending` can be approved.

**Recommended Workflow:**
1. Preview fix with `/fixes/{id}/preview`
2. Review sandbox validation results
3. Approve if all checks pass
4. Apply with `/fixes/{id}/apply`
    """,
    responses={
        200: {"description": "Fix approved successfully"},
        400: {"description": "Fix is not pending or invalid ID format"},
        404: {"description": "Fix not found"},
    },
)
async def approve_fix(
    fix_id: str,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Approve a fix proposal for application."""
    repo = FixRepository(db)
    try:
        fix = await repo.get_by_id(UUID(fix_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fix ID format")

    if fix is None:
        raise HTTPException(status_code=404, detail="Fix not found")

    if fix.status != FixStatus.PENDING:
        raise HTTPException(status_code=400, detail="Fix is not pending")

    fix = await repo.update_status(UUID(fix_id), FixStatus.APPROVED)
    return {"data": _fix_to_dict(fix), "success": True}


@router.post(
    "/{fix_id}/reject",
    summary="Reject fix",
    description="""
Reject a fix proposal with a reason.

Marks the fix as rejected and records the rejection reason as a comment.
Only fixes with status `pending` can be rejected.

**When to Reject:**
- Fix introduces bugs or breaks tests
- Fix doesn't address the root cause
- Better alternative exists
- Change is not appropriate for the codebase
    """,
    responses={
        200: {"description": "Fix rejected successfully"},
        400: {"description": "Fix is not pending or invalid ID format"},
        404: {"description": "Fix not found"},
    },
)
async def reject_fix(
    fix_id: str,
    request: RejectRequest,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reject a fix proposal with a reason."""
    repo = FixRepository(db)
    try:
        fix_uuid = UUID(fix_id)
        fix = await repo.get_by_id(fix_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fix ID format")

    if fix is None:
        raise HTTPException(status_code=404, detail="Fix not found")

    if fix.status != FixStatus.PENDING:
        raise HTTPException(status_code=400, detail="Fix is not pending")

    fix = await repo.update_status(fix_uuid, FixStatus.REJECTED)

    # Store rejection reason as a comment in database
    db_user = await _get_db_user(db, user.user_id)
    if db_user:
        await repo.add_comment(
            fix_id=fix_uuid,
            user_id=db_user.id,
            content=f"Rejected: {request.reason}",
        )
        logger.info(f"Added rejection comment for fix {fix_id} by user {db_user.id}")
    else:
        # Fallback to in-memory if user not found in DB
        logger.warning(f"User {user.user_id} not found in DB, storing comment in memory")
        if fix_id not in _comments_store:
            _comments_store[fix_id] = []
        _comments_store[fix_id].append({
            "id": f"reject-{fix_id}-{datetime.utcnow().timestamp()}",
            "fix_id": fix_id,
            "author": user.user_id,
            "content": f"Rejected: {request.reason}",
            "created_at": datetime.utcnow().isoformat(),
        })

    return {"data": _fix_to_dict(fix), "success": True}


@router.post(
    "/{fix_id}/apply",
    summary="Apply fix to codebase",
    description="""
Apply an approved fix to the repository.

**Requires:** Fix must be in `approved` status.

**Modes:**

1. **GitHub PR Mode** (SaaS - automatic if repository has GitHub App installed):
   - Creates a new branch from the repository's default branch
   - Commits the fix to the new branch
   - Opens a Pull Request for review
   - Returns PR URL and number in the response

2. **Local Mode** (requires `repository_path`):
   - Applies code changes directly to files on the local filesystem
   - Optionally creates git branch and commit

3. **Status-only Mode** (no GitHub integration, no `repository_path`):
   - Only updates the fix status to `applied`
   - For manual application tracking

**Options:**
- `repository_path`: Local path to apply changes (for local mode)
- `create_branch`: Create a new branch for review (default: true, local mode only)
- `commit`: Create a git commit (default: true, local mode only)

**Response includes:**
- `data`: Updated fix object
- `success`: Boolean indicating success
- `pr`: (GitHub mode only) Object with `pr_number`, `pr_url`, and `branch`
    """,
    responses={
        200: {"description": "Fix applied successfully"},
        400: {
            "description": "Fix not approved or repository path invalid",
            "content": {
                "application/json": {
                    "example": {"detail": "Fix must be approved before applying"}
                }
            },
        },
        404: {"description": "Fix not found"},
        500: {"description": "Failed to apply fix or create GitHub PR"},
    },
)
async def apply_fix(
    fix_id: str,
    request: Optional[ApplyFixRequest] = None,
    org: Organization = Depends(enforce_feature("auto_fix")),
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Apply an approved fix to the repository."""
    from pathlib import Path
    from repotoire.autofix.applicator import FixApplicator
    from repotoire.autofix.models import (
        FixProposal as AutofixProposal,
        CodeChange as AutofixCodeChange,
        Evidence as AutofixEvidence,
        FixStatus as AutofixStatus,
        FixConfidence as AutofixConfidence,
        FixType as AutofixType,
    )
    from repotoire.models import Finding, Severity
    from repotoire.db.models.analysis import AnalysisRun
    from repotoire.db.models.repository import Repository

    repo = FixRepository(db)
    try:
        fix_uuid = UUID(fix_id)
        fix = await repo.get_by_id(fix_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fix ID format")

    if fix is None:
        raise HTTPException(status_code=404, detail="Fix not found")

    if fix.status != FixStatus.APPROVED:
        raise HTTPException(status_code=400, detail="Fix must be approved before applying")

    pr_result = None  # Track GitHub PR result if created

    # Try to get repository info for GitHub PR creation
    github_repo = None
    if fix.analysis_run_id:
        # Look up analysis run with repository eagerly loaded in a single query
        analysis_run_result = await db.execute(
            select(AnalysisRun)
            .options(selectinload(AnalysisRun.repository))
            .where(AnalysisRun.id == fix.analysis_run_id)
        )
        analysis_run = analysis_run_result.scalar_one_or_none()
        if analysis_run and analysis_run.repository:
            github_repo = analysis_run.repository

    # If we have a GitHub-connected repository, create a PR
    if github_repo and github_repo.github_repo_id:
        try:
            from repotoire.api.shared.services.github import GitHubAppClient
            from repotoire.db.models import GitHubRepository, GitHubInstallation

            # Look up the CURRENT installation ID from GitHubInstallation table
            # (Repository.github_installation_id may be stale if app was reinstalled)
            github_repo_result = await db.execute(
                select(GitHubRepository)
                .join(GitHubInstallation)
                .where(GitHubRepository.repo_id == github_repo.github_repo_id)
            )
            gh_repo = github_repo_result.scalar_one_or_none()

            if not gh_repo:
                logger.warning(f"No GitHubRepository found for repo_id {github_repo.github_repo_id}")
                raise HTTPException(
                    status_code=400,
                    detail="Repository not connected to GitHub App. Please install the GitHub App first."
                )

            # Get the installation to get the current installation_id
            installation_result = await db.execute(
                select(GitHubInstallation).where(GitHubInstallation.id == gh_repo.installation_id)
            )
            installation = installation_result.scalar_one_or_none()

            if not installation:
                logger.warning(f"No GitHubInstallation found for GitHubRepository {gh_repo.id}")
                raise HTTPException(
                    status_code=400,
                    detail="GitHub App installation not found. Please reinstall the GitHub App."
                )

            github_client = GitHubAppClient()

            # Parse owner/repo from full_name (e.g., "owner/repo")
            if not github_repo.full_name or "/" not in github_repo.full_name:
                logger.error(f"Invalid repository full_name format: {github_repo.full_name}")
                raise HTTPException(
                    status_code=400,
                    detail="Invalid repository configuration - missing owner/repo format"
                )
            owner, repo_name = github_repo.full_name.split("/", 1)

            # Create unique branch name
            fix_branch = f"repotoire/fix-{fix.fix_type.value}-{str(fix.id)[:8]}"

            pr_result = await github_client.create_fix_pr(
                installation_id=installation.installation_id,  # Use fresh installation_id
                owner=owner,
                repo=repo_name,
                base_branch=github_repo.default_branch,
                fix_branch=fix_branch,
                file_path=fix.file_path,
                fixed_code=fix.fixed_code,
                title=fix.title,
                description=fix.description,
            )

            logger.info(
                f"Created GitHub PR #{pr_result['pr_number']} for fix {fix_id}: {pr_result['pr_url']}"
            )

        except Exception as e:
            logger.error(f"Failed to create GitHub PR for fix {fix_id}: {e}")
            # Don't mark fix as FAILED here - leave it as APPROVED so user can retry
            # The fix status only changes to APPLIED on success, or user can reject
            raise HTTPException(
                status_code=500,
                detail=f"Failed to create GitHub PR: {str(e)}. The fix remains approved - please try again."
            )

    # If repository_path provided (local mode), apply the fix to filesystem
    elif request and request.repository_path:
        repository_path = Path(request.repository_path)

        if not repository_path.exists():
            raise HTTPException(status_code=400, detail=f"Repository path does not exist: {repository_path}")

        # Convert DB Fix to autofix FixProposal
        proposal = AutofixProposal(
            id=str(fix.id),
            finding=Finding(
                id=str(fix.finding_id) if fix.finding_id else "unknown",
                title=fix.title,
                description=fix.description,
                severity=Severity.MEDIUM,
                detector="manual",
                affected_files=[fix.file_path],
            ),
            fix_type=AutofixType(fix.fix_type.value),
            confidence=AutofixConfidence(fix.confidence.value),
            changes=[
                AutofixCodeChange(
                    file_path=Path(fix.file_path),
                    original_code=fix.original_code,
                    fixed_code=fix.fixed_code,
                    start_line=fix.line_start or 0,
                    end_line=fix.line_end or 0,
                    description=fix.description,
                )
            ],
            title=fix.title,
            description=fix.description,
            rationale=fix.explanation,
            evidence=AutofixEvidence(
                similar_patterns=fix.evidence.get("similar_patterns", []) if fix.evidence else [],
                documentation_refs=fix.evidence.get("documentation_refs", []) if fix.evidence else [],
                best_practices=fix.evidence.get("best_practices", []) if fix.evidence else [],
            ),
            status=AutofixStatus.APPROVED,
            branch_name=f"autofix/{fix.fix_type.value}/{fix.id}",
            commit_message=f"fix: {fix.title}\n\n{fix.description}",
        )

        # Apply the fix using FixApplicator
        applicator = FixApplicator(
            repository_path=repository_path,
            create_branch=request.create_branch,
        )

        success, error = applicator.apply_fix(proposal, commit=request.commit)

        if not success:
            # Mark as failed in database
            fix = await repo.update_status(fix_uuid, FixStatus.FAILED)
            raise HTTPException(status_code=500, detail=f"Failed to apply fix: {error}")

        logger.info(f"Successfully applied fix {fix_id} to {repository_path}")

    # Mark as applied in database
    fix = await repo.update_status(fix_uuid, FixStatus.APPLIED)

    # Sync finding status to RESOLVED (maintains data consistency)
    finding_synced = await repo.sync_finding_status_on_apply(
        fix_id=fix_uuid,
        changed_by=user.user_id,
    )
    if finding_synced:
        logger.info(f"Synced finding status to RESOLVED for fix {fix_id}")

    result = {"data": _fix_to_dict(fix), "success": True}

    # Include PR info if created
    if pr_result:
        result["pr"] = pr_result

    return result


# In-memory cache for preview results
_preview_cache: dict[str, tuple[PreviewResult, str]] = {}  # fix_id -> (result, fix_hash)


def _get_fix_hash(fix: FixProposal) -> str:
    """Generate a hash for fix content to detect changes."""
    import hashlib
    content = "".join(
        f"{c.file_path}:{c.fixed_code}" for c in fix.changes
    )
    return hashlib.md5(content.encode()).hexdigest()[:16]


async def _get_preview_cache():
    """Lazy import to avoid circular dependency."""
    from repotoire.cache import get_preview_cache

    return await get_preview_cache()


@router.post(
    "/{fix_id}/preview",
    response_model=PreviewResult,
    summary="Preview fix in sandbox",
    description="""
Run a fix preview in an isolated E2B sandbox to validate before approving.

**Validation Checks:**
1. **Syntax** - Validates Python syntax using AST parser
2. **Imports** - Verifies all imports can be resolved
3. **Types** (optional) - Runs mypy type checking
4. **Tests** (optional) - Runs test suite with the fix applied

**Sandbox Environment:**
- Isolated Firecracker microVM
- No network access to your infrastructure
- Automatic cleanup after execution
- ~30 second timeout

**Caching:**
Results are cached in Redis. Subsequent calls return cached results
unless the fix content changes.

**Without E2B Configured:**
Falls back to local syntax-only validation (import/type checks skipped).
    """,
    responses={
        200: {"description": "Preview completed successfully"},
        404: {"description": "Fix not found"},
        500: {"description": "Preview execution failed"},
    },
)
async def preview_fix(
    fix_id: str,
    force: bool = Query(False, description="Force fresh preview, bypassing cache"),
    org: Organization = Depends(enforce_feature("auto_fix")),
    user: ClerkUser = Depends(get_current_user),
    cache: "PreviewCache" = Depends(_get_preview_cache),
    db: AsyncSession = Depends(get_db),
) -> PreviewResult:
    """Run fix preview in sandbox to validate before approving."""
    fix: Optional[FixProposal] = None
    db_fix_obj: Optional[Fix] = None  # Keep reference to database fix for analysis_run_id

    # First try to get from database
    try:
        fix_uuid = UUID(fix_id)
        repo = FixRepository(db)
        db_fix = await repo.get_by_id(fix_uuid)
        if db_fix:
            fix = _db_fix_to_preview_proposal(db_fix)
            db_fix_obj = db_fix  # Keep reference for later
            logger.debug(f"Found fix {fix_id} in database")
    except ValueError:
        # Not a valid UUID, check in-memory store
        pass

    # Fall back to in-memory store (for Best-of-N fixes)
    if fix is None and fix_id in _fixes_store:
        fix = _fixes_store[fix_id]
        logger.debug(f"Found fix {fix_id} in memory store")

    if fix is None:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix_hash = _get_fix_hash(fix)

    # Check in-memory cache first
    if fix_id in _preview_cache:
        cached_result, cached_hash = _preview_cache[fix_id]
        if cached_hash == fix_hash:
            logger.debug(f"Preview cache hit (in-memory) for fix {fix_id}")
            return cached_result

    # Check Redis cache
    cached_result = await cache.get_preview(fix_id)
    if cached_result:
        # Validate the hash from cached_at field
        if cached_result.cached_at and ":" in cached_result.cached_at:
            _, cached_hash = cached_result.cached_at.rsplit(":", 1)
            if cached_hash == fix_hash:
                logger.debug(f"Preview cache hit (Redis) for fix {fix_id}")
                _preview_cache[fix_id] = (cached_result, fix_hash)
                return cached_result

    start_time = time.time()
    checks: List[PreviewCheck] = []
    stdout_parts: List[str] = []
    stderr_parts: List[str] = []

    # Try to get full file content for proper validation
    # (snippet-only validation fails when imports are defined elsewhere in file)
    full_file_contents: dict[str, str] = {}  # file_path -> full content with fix applied
    stale_files: list[str] = []  # files where original code not found (fix is stale)

    try:
        # Get repository info for GitHub file fetching
        from repotoire.db.models.repository import Repository
        from repotoire.db.models import AnalysisRun, GitHubRepository, GitHubInstallation

        if db_fix_obj and db_fix_obj.analysis_run_id:
            analysis_run = await db.get(AnalysisRun, db_fix_obj.analysis_run_id)
            if analysis_run and analysis_run.repository_id:
                github_repo = await db.get(Repository, analysis_run.repository_id)

                if github_repo and github_repo.github_repo_id:
                    # Look up fresh installation ID
                    github_repo_result = await db.execute(
                        select(GitHubRepository)
                        .where(GitHubRepository.repo_id == github_repo.github_repo_id)
                    )
                    gh_repo = github_repo_result.scalar_one_or_none()

                    if gh_repo:
                        installation_result = await db.execute(
                            select(GitHubInstallation).where(GitHubInstallation.id == gh_repo.installation_id)
                        )
                        installation = installation_result.scalar_one_or_none()

                        if installation:
                            from repotoire.api.shared.services.github import GitHubAppClient
                            github_client = GitHubAppClient()
                            owner, repo_name = github_repo.full_name.split("/", 1)

                            # Fetch full file content for each change
                            for change in fix.changes:
                                try:
                                    access_token, _ = await github_client.get_installation_token(
                                        installation.installation_id
                                    )
                                    file_content = await github_client.get_file_content(
                                        access_token=access_token,
                                        owner=owner,
                                        repo=repo_name,
                                        path=change.file_path,
                                        ref=github_repo.default_branch or "main",
                                    )

                                    if file_content:
                                        # Apply the fix to get complete file
                                        lines = file_content.split('\n')

                                        # First, verify the original code still exists at the expected location
                                        # If the file has changed, the line numbers may be stale
                                        original_lines = change.original_code.split('\n') if change.original_code else []
                                        line_start = change.start_line or 1
                                        line_end = change.end_line or len(lines)

                                        # Check if original code matches at expected location
                                        original_code_found = False
                                        actual_start = line_start

                                        if original_lines and line_start <= len(lines):
                                            # Check if original code is at expected location
                                            expected_lines = lines[line_start - 1:line_end]
                                            # Compare first line (stripped) to detect match
                                            if expected_lines and original_lines:
                                                first_orig = original_lines[0].strip()
                                                first_file = expected_lines[0].strip() if expected_lines else ""
                                                if first_orig and first_orig in first_file or first_file in first_orig:
                                                    original_code_found = True
                                                    logger.info(f"Original code found at expected location (lines {line_start}-{line_end})")

                                        if not original_code_found and original_lines:
                                            # Search for original code elsewhere in the file
                                            first_orig_stripped = original_lines[0].strip()
                                            for i, line in enumerate(lines):
                                                if first_orig_stripped and first_orig_stripped in line.strip():
                                                    # Code found at different location - treat as stale
                                                    # We can't safely adjust line numbers without knowing actual scope
                                                    logger.warning(f"Original code found at line {i + 1} (expected {line_start}) - marking as stale")
                                                    stale_files.append(change.file_path)
                                                    break

                                        if change.file_path in stale_files:
                                            continue

                                        if not original_code_found and original_lines and original_lines[0].strip():
                                            # Original code not found - file has changed significantly
                                            logger.warning(f"Original code not found in {change.file_path} - file may have changed since fix was generated")
                                            logger.warning(f"  Looking for: {repr(original_lines[0][:60])}")
                                            # Track this file as stale
                                            stale_files.append(change.file_path)
                                            # Skip this file - we can't safely apply the fix
                                            continue

                                        line_start = actual_start

                                        # Now apply indentation from the target location
                                        fixed_lines = change.fixed_code.split('\n')

                                        # Get indentation from the line we're replacing
                                        if line_start <= len(lines):
                                            target_line = lines[line_start - 1]
                                            base_indent = target_line[:len(target_line) - len(target_line.lstrip())]

                                            # Check if fixed code already has indentation
                                            fixed_has_indent = fixed_lines and fixed_lines[0] and not fixed_lines[0][0].isalnum()

                                            if base_indent and not fixed_has_indent:
                                                # Add base indentation to all non-empty lines
                                                fixed_lines = [
                                                    base_indent + line if line.strip() else line
                                                    for line in fixed_lines
                                                ]
                                                logger.info(f"Applied {len(base_indent)} chars of indentation to fixed code")

                                        logger.info(f"Constructing full file: {change.file_path}")
                                        logger.info(f"  Replacing lines {line_start}-{line_end}")

                                        new_lines = lines[:line_start - 1] + fixed_lines + lines[line_end:]
                                        full_file_contents[change.file_path] = '\n'.join(new_lines)
                                        logger.info(f"  Constructed file: {len(new_lines)} lines")
                                except Exception as e:
                                    logger.warning(f"Failed to fetch file {change.file_path} from GitHub: {e}")
    except Exception as e:
        logger.warning(f"Failed to get repository info for full file validation: {e}")

    try:
        # Import sandbox components
        from repotoire.sandbox import (
            CodeValidator,
            ValidationConfig,
            SandboxConfig,
            SandboxConfigurationError,
        )

        # Create validation config
        validation_config = ValidationConfig(
            run_import_check=True,
            run_type_check=False,  # Type check is slower, make optional
            run_smoke_test=False,
            timeout_seconds=30,
        )

        # Check if sandbox is configured
        sandbox_config = SandboxConfig.from_env()

        if not sandbox_config.is_configured:
            # Run syntax-only validation locally without sandbox
            logger.info("E2B not configured, running syntax-only validation")

            for change in fix.changes:
                check_start = time.time()
                try:
                    ast.parse(change.fixed_code)
                    checks.append(PreviewCheck(
                        name="syntax",
                        passed=True,
                        message=f"Syntax valid for {change.file_path}",
                        duration_ms=int((time.time() - check_start) * 1000),
                    ))
                except SyntaxError as e:
                    checks.append(PreviewCheck(
                        name="syntax",
                        passed=False,
                        message=f"SyntaxError in {change.file_path}: {e.msg} (line {e.lineno})",
                        duration_ms=int((time.time() - check_start) * 1000),
                    ))
                    stderr_parts.append(f"SyntaxError: {e.msg}")

            # Add warning about limited validation
            checks.append(PreviewCheck(
                name="import",
                passed=True,
                message="Import validation skipped (E2B sandbox not configured)",
                duration_ms=0,
            ))

            success = all(c.passed for c in checks if c.name == "syntax")
            duration_ms = int((time.time() - start_time) * 1000)

            result = PreviewResult(
                success=success,
                stdout="\n".join(stdout_parts),
                stderr="\n".join(stderr_parts),
                duration_ms=duration_ms,
                checks=checks,
                error=None if success else "Syntax validation failed",
            )

            # Cache the result with hash embedded in cached_at for validation
            cached_at_with_hash = f"{datetime.utcnow().isoformat()}:{fix_hash}"
            cached_result = PreviewResult(
                success=result.success,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=result.duration_ms,
                checks=result.checks,
                error=result.error,
                cached_at=cached_at_with_hash,
            )

            # Store in Redis cache
            await cache.set_preview(fix_id, cached_result)

            # Also store in in-memory cache as fallback
            _preview_cache[fix_id] = (cached_result, fix_hash)

            return result

        # Check for stale files first - return clear error if fix is outdated
        if stale_files:
            for stale_file in stale_files:
                checks.append(PreviewCheck(
                    name="stale",
                    passed=False,
                    message=f"Fix is outdated - original code not found in {stale_file}. The file may have been modified since this fix was generated.",
                    duration_ms=0,
                ))
                stderr_parts.append(f"StaleFixError: Original code not found in {stale_file}")

            # Mark the fix as stale in the database so it's filtered from the list
            try:
                await repo.update_status(fix.id, FixStatus.STALE, validate_transition=False)
                logger.info(f"Marked fix {fix_id} as stale (code changed in: {', '.join(stale_files)})")
            except Exception as e:
                logger.warning(f"Failed to mark fix {fix_id} as stale: {e}")

            duration_ms = int((time.time() - start_time) * 1000)
            result = PreviewResult(
                success=False,
                stdout="\n".join(stdout_parts),
                stderr="\n".join(stderr_parts),
                duration_ms=duration_ms,
                checks=checks,
                error="Fix is outdated - the target code has been modified since this fix was generated. Consider regenerating the fix.",
            )
            await cache.set_preview(fix_id, result)
            return result

        # Full sandbox validation
        async with CodeValidator(validation_config, sandbox_config) as validator:
            for change in fix.changes:
                file_path = str(change.file_path)

                # Use full file content if available, otherwise fall back to snippet
                # Full file validation is more accurate as it includes all imports
                code_to_validate = full_file_contents.get(change.file_path, change.fixed_code)
                if change.file_path in full_file_contents:
                    logger.debug(f"Using full file content for validation of {file_path}")
                else:
                    logger.debug(f"Using snippet-only validation for {file_path} (full file not available)")

                validation_result = await validator.validate(
                    fixed_code=code_to_validate,
                    file_path=file_path,
                    original_code=change.original_code,
                )

                # Add syntax check result
                checks.append(PreviewCheck(
                    name="syntax",
                    passed=validation_result.syntax_valid,
                    message=(
                        f"Syntax valid for {file_path}"
                        if validation_result.syntax_valid
                        else f"Syntax error in {file_path}: {validation_result.errors[0].message if validation_result.errors else 'Unknown'}"
                    ),
                    duration_ms=5,  # Syntax check is fast
                ))

                # Add import check result
                if validation_result.import_valid is not None:
                    import_errors = [
                        e for e in validation_result.errors
                        if e.level == "import"
                    ]
                    checks.append(PreviewCheck(
                        name="import",
                        passed=validation_result.import_valid,
                        message=(
                            f"Imports valid for {file_path}"
                            if validation_result.import_valid
                            else f"Import error: {import_errors[0].message if import_errors else 'Unknown'}"
                            + (f" {import_errors[0].suggestion}" if import_errors and import_errors[0].suggestion else "")
                        ),
                        duration_ms=validation_result.duration_ms - 5,
                    ))

                # Add type check result if available
                if validation_result.type_valid is not None:
                    type_errors = [
                        e for e in validation_result.errors
                        if e.level == "type"
                    ]
                    checks.append(PreviewCheck(
                        name="type",
                        passed=validation_result.type_valid,
                        message=(
                            f"Type check passed for {file_path}"
                            if validation_result.type_valid
                            else f"Type error: {type_errors[0].message if type_errors else 'Unknown'}"
                        ),
                        duration_ms=100,  # Estimate
                    ))

                # Collect errors for stderr
                for error in validation_result.errors:
                    stderr_parts.append(f"{error.error_type}: {error.message}")

        success = all(c.passed for c in checks)
        duration_ms = int((time.time() - start_time) * 1000)

        result = PreviewResult(
            success=success,
            stdout="\n".join(stdout_parts),
            stderr="\n".join(stderr_parts),
            duration_ms=duration_ms,
            checks=checks,
            error=None,
        )

        # Cache the result with hash embedded in cached_at for validation
        cached_at_with_hash = f"{datetime.utcnow().isoformat()}:{fix_hash}"
        cached_result = PreviewResult(
            success=result.success,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
            checks=result.checks,
            error=result.error,
            cached_at=cached_at_with_hash,
        )

        # Store in Redis cache
        await cache.set_preview(fix_id, cached_result)

        # Also store in in-memory cache as fallback
        _preview_cache[fix_id] = (cached_result, fix_hash)

        logger.info(f"Preview completed for fix {fix_id}: success={success}")
        return result

    except SandboxConfigurationError as e:
        logger.warning(f"Sandbox not configured: {e}")
        duration_ms = int((time.time() - start_time) * 1000)
        return PreviewResult(
            success=False,
            stdout="",
            stderr=str(e),
            duration_ms=duration_ms,
            checks=[],
            error=f"Sandbox not configured: {e}",
        )

    except Exception as e:
        logger.exception(f"Preview failed for fix {fix_id}: {e}")
        duration_ms = int((time.time() - start_time) * 1000)
        return PreviewResult(
            success=False,
            stdout="",
            stderr=str(e),
            duration_ms=duration_ms,
            checks=[],
            error=f"Preview execution failed: {str(e)}",
        )


@router.post("/{fix_id}/comment")
async def add_comment(fix_id: str, request: CommentCreate, user: ClerkUser = Depends(get_current_user)) -> dict:
    """Add a comment to a fix."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    comment_id = f"comment-{fix_id}-{datetime.utcnow().timestamp()}"
    comment = {
        "id": comment_id,
        "fix_id": fix_id,
        "author": user.user_id,
        "content": request.content,
        "created_at": datetime.utcnow().isoformat(),
    }

    if fix_id not in _comments_store:
        _comments_store[fix_id] = []
    _comments_store[fix_id].append(comment)

    return {"data": comment, "success": True}


@router.get("/{fix_id}/comments")
async def get_comments(
    fix_id: str,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(25, ge=1, le=100),
) -> List[dict]:
    """Get comments for a fix."""
    repo = FixRepository(db)
    try:
        fix_uuid = UUID(fix_id)
        fix = await repo.get_by_id(fix_uuid)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid fix ID format")

    if fix is None:
        raise HTTPException(status_code=404, detail="Fix not found")

    # Get comments from database
    comments = await repo.get_comments(fix_uuid, limit=limit)

    # Convert to dict format
    return [
        {
            "id": str(c.id),
            "fix_id": str(c.fix_id),
            "author": c.user.email if c.user else "Unknown",
            "content": c.content,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in comments
    ]


@router.post("/batch/approve")
async def batch_approve(request: BatchRequest, user: ClerkUser = Depends(get_current_user)) -> dict:
    """Batch approve multiple fixes."""
    approved = 0
    for fix_id in request.ids:
        if fix_id in _fixes_store:
            fix = _fixes_store[fix_id]
            if fix.status == FixStatus.PENDING:
                fix.status = FixStatus.APPROVED
                approved += 1

    return {"data": {"approved": approved}, "success": True}


@router.post("/batch/reject")
async def batch_reject(request: BatchRejectRequest, user: ClerkUser = Depends(get_current_user)) -> dict:
    """Batch reject multiple fixes."""
    rejected = 0
    for fix_id in request.ids:
        if fix_id in _fixes_store:
            fix = _fixes_store[fix_id]
            if fix.status == FixStatus.PENDING:
                fix.status = FixStatus.REJECTED
                rejected += 1
                # Add rejection comment
                comment_id = f"reject-{fix_id}-{datetime.utcnow().timestamp()}"
                if fix_id not in _comments_store:
                    _comments_store[fix_id] = []
                _comments_store[fix_id].append({
                    "id": comment_id,
                    "fix_id": fix_id,
                    "author": "System",
                    "content": f"Batch rejected: {request.reason}",
                    "created_at": datetime.utcnow().isoformat(),
                })

    return {"data": {"rejected": rejected}, "success": True}


# =============================================================================
# Best-of-N Endpoints
# =============================================================================


class BestOfNFixRequest(BaseModel):
    """Request for Best-of-N fix generation."""

    finding_id: str = Field(description="ID of the finding to fix")
    repository_path: str = Field(description="Path to the repository")
    n: int = Field(default=5, ge=2, le=10, description="Number of candidates to generate")
    test_command: str = Field(default="pytest", description="Test command to run")


class BestOfNFixResponse(BaseModel):
    """Response from Best-of-N fix generation."""

    ranked_fixes: List[dict] = Field(description="Ranked list of fix candidates")
    best_fix: Optional[dict] = Field(description="Best fix (highest ranked)")
    candidates_generated: int
    candidates_verified: int
    total_duration_ms: int
    total_sandbox_cost_usd: float
    has_recommendation: bool


class BestOfNStatusResponse(BaseModel):
    """Status of Best-of-N feature for a customer."""

    is_available: bool = Field(description="Whether Best-of-N is available")
    access_type: str = Field(description="Access type: unavailable, addon, or included")
    addon_enabled: bool = Field(description="Whether Pro add-on is enabled")
    max_n: int = Field(description="Maximum candidates allowed")
    monthly_runs_limit: int = Field(description="Monthly runs limit (-1 = unlimited)")
    monthly_runs_used: int = Field(description="Runs used this month")
    remaining_runs: int = Field(description="Remaining runs (-1 = unlimited)")
    addon_price: Optional[str] = Field(description="Add-on price (for Pro tier)")
    upgrade_url: Optional[str] = Field(description="URL to upgrade (for Free tier)")
    addon_url: Optional[str] = Field(description="URL to enable add-on (for Pro tier)")


class FeatureNotAvailableError(BaseModel):
    """Error response when feature is not available."""

    error: str = "feature_not_available"
    message: str
    upgrade_url: Optional[str] = None
    addon_url: Optional[str] = None


class UsageLimitError(BaseModel):
    """Error response when usage limit is exceeded."""

    error: str = "usage_limit_exceeded"
    message: str
    used: int
    limit: int
    resets_at: str


@router.get("/best-of-n/status")
async def get_best_of_n_status(
    user: ClerkUser = Depends(get_current_user),
) -> BestOfNStatusResponse:
    """Get customer's Best-of-N feature status and usage.

    Returns information about:
    - Whether Best-of-N is available for the user's tier
    - Current usage and limits
    - Pricing for add-on (Pro tier)
    - Upgrade URLs (Free tier)
    """
    # In production, get tier from user's organization
    # For now, default to FREE if not available
    tier = getattr(user, "tier", None) or PlanTier.FREE

    # Get entitlement (without DB for now)
    entitlement = await get_customer_entitlement(
        customer_id=user.user_id,
        tier=tier,
        db=None,  # Pass actual db session in production
    )

    return BestOfNStatusResponse(
        is_available=entitlement.is_available,
        access_type=entitlement.access.value,
        addon_enabled=entitlement.addon_enabled,
        max_n=entitlement.max_n,
        monthly_runs_limit=entitlement.monthly_runs_limit,
        monthly_runs_used=entitlement.monthly_runs_used,
        remaining_runs=entitlement.remaining_runs,
        addon_price=entitlement.addon_price,
        upgrade_url=entitlement.upgrade_url,
        addon_url=entitlement.addon_url,
    )


@router.post("/best-of-n")
async def generate_best_of_n_fix(
    request: BestOfNFixRequest,
    user: ClerkUser = Depends(get_current_user),
) -> BestOfNFixResponse:
    """Generate N fix candidates using Best-of-N sampling.

    This endpoint:
    1. Checks if user has access to Best-of-N (Pro add-on or Enterprise)
    2. Generates N fix candidates with varied approaches
    3. Verifies each in parallel E2B sandboxes
    4. Returns ranked fixes by test pass rate and quality

    Availability:
    - Free tier: Not available (403)
    - Pro tier: Requires $29/month add-on
    - Enterprise tier: Included free

    Returns:
        BestOfNFixResponse with ranked fixes

    Raises:
        403: Feature not available or add-on not enabled
        429: Monthly usage limit exceeded
    """
    # Get user's tier (in production, from organization)
    tier = getattr(user, "tier", None) or PlanTier.FREE

    # Get entitlement
    entitlement = await get_customer_entitlement(
        customer_id=user.user_id,
        tier=tier,
        db=None,  # Pass actual db session in production
    )

    # Create generator with entitlement checks
    config = BestOfNConfig(n=request.n)
    generator = BestOfNGenerator(
        config=config,
        customer_id=user.user_id,
        tier=tier,
        entitlement=entitlement,
        db=None,  # Pass actual db session in production
    )

    try:
        # Get the finding from store (in production, from database)
        finding = None
        for fix in _fixes_store.values():
            if hasattr(fix.finding, "id") and fix.finding.id == request.finding_id:
                finding = fix.finding
                break

        if finding is None:
            raise HTTPException(
                status_code=404,
                detail=f"Finding {request.finding_id} not found",
            )

        # Generate and verify fixes
        result = await generator.generate_and_verify(
            issue=finding,
            repository_path=request.repository_path,
            test_command=request.test_command,
        )

        # Store generated fixes
        for ranked in result.ranked_fixes:
            _fixes_store[ranked.fix.id] = ranked.fix

        return BestOfNFixResponse(
            ranked_fixes=[rf.to_dict() for rf in result.ranked_fixes],
            best_fix=result.best_fix.to_dict() if result.best_fix else None,
            candidates_generated=result.candidates_generated,
            candidates_verified=result.candidates_verified,
            total_duration_ms=result.total_duration_ms,
            total_sandbox_cost_usd=result.total_sandbox_cost_usd,
            has_recommendation=result.best_fix is not None and result.best_fix.is_recommended,
        )

    except BestOfNNotAvailableError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "feature_not_available",
                "message": e.message,
                "upgrade_url": e.upgrade_url,
                "addon_url": e.addon_url,
            },
        )

    except BestOfNUsageLimitError as e:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "usage_limit_exceeded",
                "message": e.message,
                "used": e.used,
                "limit": e.limit,
                "resets_at": e.resets_at.isoformat(),
            },
        )


@router.post("/best-of-n/{fix_id}/select")
async def select_best_of_n_fix(
    fix_id: str,
    user: ClerkUser = Depends(get_current_user),
) -> dict:
    """Select a fix from Best-of-N candidates.

    Marks the selected fix as approved and others as rejected.

    Args:
        fix_id: ID of the fix to select

    Returns:
        Selected fix details
    """
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix = _fixes_store[fix_id]

    # Find related candidates (same base ID)
    base_id = fix_id.rsplit("_candidate_", 1)[0]
    related_ids = [
        fid for fid in _fixes_store.keys()
        if fid.startswith(base_id) and fid != fix_id
    ]

    # Approve selected fix
    fix.status = FixStatus.APPROVED

    # Reject other candidates
    for other_id in related_ids:
        other_fix = _fixes_store.get(other_id)
        if other_fix and other_fix.status == FixStatus.PENDING:
            other_fix.status = FixStatus.REJECTED

    logger.info(
        f"Selected Best-of-N fix {fix_id}",
        extra={
            "user_id": user.user_id,
            "rejected_count": len(related_ids),
        },
    )

    return {
        "data": fix.to_dict(),
        "success": True,
        "rejected_count": len(related_ids),
    }


# =============================================================================
# Generate Fixes for Analysis
# =============================================================================


class GenerateFixesRequest(BaseModel):
    """Request to generate fixes for an analysis run."""

    finding_ids: Optional[List[str]] = Field(
        default=None,
        description="Specific finding IDs to generate fixes for. If provided, ignores severity_filter."
    )
    max_fixes: int = Field(default=10, ge=1, le=50, description="Maximum number of fixes to generate")
    severity_filter: Optional[List[str]] = Field(
        default=["critical", "high"],
        description="Severities to process (critical, high, medium, low, info). Ignored if finding_ids provided."
    )


class GenerateFixesResponse(BaseModel):
    """Response from fix generation request."""

    status: str = Field(description="Task status: queued, skipped, or error")
    message: str = Field(description="Human readable message")
    task_id: Optional[str] = Field(default=None, description="Celery task ID if queued")


class ConsistencyStatsResponse(BaseModel):
    """Response with data consistency statistics."""

    orphaned_fixes: int = Field(..., description="Fixes with NULL finding_id")
    status_mismatches: int = Field(..., description="Applied fixes where finding is not resolved")
    needs_attention: bool = Field(..., description="Whether any consistency issues exist")


@router.post("/generate/{analysis_run_id}")
async def generate_fixes(
    analysis_run_id: str,
    request: GenerateFixesRequest = GenerateFixesRequest(),
    org: Organization = Depends(enforce_feature("auto_fix")),
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GenerateFixesResponse:
    """Trigger AI fix generation for an analysis run.

    Queues a background task to generate fix proposals for high-severity
    findings from the specified analysis run. Fixes are generated using
    GPT-4o with RAG context from the knowledge graph.

    Requires OPENAI_API_KEY to be configured on the worker.

    Args:
        analysis_run_id: UUID of the analysis run with findings
        request: Configuration for fix generation

    Returns:
        GenerateFixesResponse with task status
    """
    from repotoire.db.models import AnalysisRun, AnalysisStatus

    # Validate analysis run exists and is completed
    try:
        run_uuid = UUID(analysis_run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid analysis run ID format")

    result = await db.execute(
        select(AnalysisRun).where(AnalysisRun.id == run_uuid)
    )
    analysis = result.scalar_one_or_none()

    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis run not found")

    if analysis.status != AnalysisStatus.COMPLETED:
        return GenerateFixesResponse(
            status="skipped",
            message=f"Analysis is not completed (status: {analysis.status.value})",
            task_id=None,
        )

    # Queue the fix generation task
    try:
        from repotoire.workers.hooks import generate_fixes_for_analysis

        task = generate_fixes_for_analysis.delay(
            analysis_run_id=analysis_run_id,
            max_fixes=request.max_fixes,
            severity_filter=request.severity_filter,
            finding_ids=request.finding_ids,
        )

        logger.info(
            f"Queued fix generation for analysis {analysis_run_id}",
            extra={"task_id": task.id, "user_id": user.user_id},
        )

        return GenerateFixesResponse(
            status="queued",
            message=f"Fix generation queued for {analysis.findings_count or 0} findings",
            task_id=task.id,
        )

    except Exception as e:
        logger.exception(f"Failed to queue fix generation: {e}")
        return GenerateFixesResponse(
            status="error",
            message=f"Failed to queue task: {str(e)}",
            task_id=None,
        )


@router.get(
    "/consistency/stats",
    response_model=ConsistencyStatsResponse,
    summary="Get data consistency statistics",
    description="""
Get statistics about data consistency between fixes and findings.

**What This Shows:**
- `orphaned_fixes`: Fixes where the linked finding was deleted (finding_id is NULL)
- `status_mismatches`: Fixes marked as APPLIED but finding is not RESOLVED
- `needs_attention`: True if any consistency issues exist

**Use Cases:**
- Monitor data health in production
- Identify issues before they cause user-facing problems
- Verify that sync tasks are working correctly

**Note:** This is an admin/monitoring endpoint. Orphaned fixes older than 30 days
are automatically cleaned up by the scheduled `cleanup_orphaned_fixes` task.
    """,
    responses={
        200: {"description": "Consistency stats retrieved successfully"},
    },
)
async def get_consistency_stats(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConsistencyStatsResponse:
    """Get data consistency statistics for fixes and findings."""
    repo = FixRepository(db)
    stats = await repo.get_consistency_stats()

    return ConsistencyStatsResponse(
        orphaned_fixes=stats["orphaned_fixes"],
        status_mismatches=stats["status_mismatches"],
        needs_attention=stats["needs_attention"],
    )


@router.post(
    "/consistency/sync",
    summary="Trigger status sync for mismatches",
    description="""
Trigger a background task to sync status mismatches between fixes and findings.

This finds all cases where a fix is APPLIED but the linked finding is not
RESOLVED, and updates the finding status to RESOLVED.

**When to Use:**
- After discovering status mismatches via `/consistency/stats`
- When migrating data from an older version
- To repair inconsistencies caused by bugs

**Note:** The sync is done asynchronously. Check the task status via the
returned task_id.
    """,
    responses={
        200: {"description": "Sync task queued successfully"},
        500: {"description": "Failed to queue sync task"},
    },
)
async def trigger_consistency_sync(
    user: ClerkUser = Depends(get_current_user),
) -> dict:
    """Trigger background task to sync status mismatches."""
    try:
        from repotoire.workers.hooks import sync_fix_finding_status_mismatches

        task = sync_fix_finding_status_mismatches.delay()

        logger.info(
            f"Queued status sync task",
            extra={"task_id": task.id, "user_id": user.user_id},
        )

        return {
            "status": "queued",
            "message": "Status sync task queued",
            "task_id": task.id,
        }

    except Exception as e:
        logger.exception(f"Failed to queue status sync: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue sync task: {str(e)}",
        )


@router.post(
    "/consistency/cleanup",
    summary="Trigger orphan cleanup",
    description="""
Trigger a background task to clean up orphaned fixes.

Orphaned fixes are those where the linked finding was deleted (finding_id is NULL).
This task deletes orphaned fixes that are older than 30 days.

**When to Use:**
- After discovering orphaned fixes via `/consistency/stats`
- During scheduled maintenance
- After bulk finding deletions

**Note:** The cleanup is done asynchronously. Check the task status via the
returned task_id.
    """,
    responses={
        200: {"description": "Cleanup task queued successfully"},
        500: {"description": "Failed to queue cleanup task"},
    },
)
async def trigger_orphan_cleanup(
    user: ClerkUser = Depends(get_current_user),
    max_age_days: int = Query(30, ge=1, le=365, description="Only delete orphans older than this many days"),
) -> dict:
    """Trigger background task to clean up orphaned fixes."""
    try:
        from repotoire.workers.hooks import cleanup_orphaned_fixes

        task = cleanup_orphaned_fixes.delay(max_age_days=max_age_days)

        logger.info(
            f"Queued orphan cleanup task",
            extra={"task_id": task.id, "user_id": user.user_id, "max_age_days": max_age_days},
        )

        return {
            "status": "queued",
            "message": f"Cleanup task queued (max_age_days={max_age_days})",
            "task_id": task.id,
        }

    except Exception as e:
        logger.exception(f"Failed to queue orphan cleanup: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to queue cleanup task: {str(e)}",
        )

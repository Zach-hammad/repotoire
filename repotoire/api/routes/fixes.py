"""API routes for fix management and Best-of-N generation."""

from __future__ import annotations

import time
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from repotoire.autofix.models import (
    FixProposal,
    FixStatus,
    FixConfidence,
    FixType,
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
from repotoire.api.auth import ClerkUser, get_current_user
from repotoire.api.models import PreviewResult, PreviewCheck
from repotoire.db.models import PlanTier
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from repotoire.cache import PreviewCache

logger = get_logger(__name__)

router = APIRouter(prefix="/fixes", tags=["fixes"])

# In-memory storage for fixes (replace with database in production)
_fixes_store: dict[str, FixProposal] = {}
_comments_store: dict[str, list] = {}


class PaginatedResponse(BaseModel):
    """Paginated response wrapper."""
    items: List[dict]
    total: int
    page: int
    page_size: int
    has_more: bool


class FixComment(BaseModel):
    """A comment on a fix."""
    id: str
    fix_id: str
    author: str
    content: str
    created_at: datetime


class CommentCreate(BaseModel):
    """Request to create a comment."""
    content: str


class RejectRequest(BaseModel):
    """Request to reject a fix."""
    reason: str


class BatchRequest(BaseModel):
    """Request for batch operations."""
    ids: List[str]


class BatchRejectRequest(BatchRequest):
    """Request for batch reject."""
    reason: str


@router.get("")
async def list_fixes(
    user: ClerkUser = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[List[FixStatus]] = Query(None),
    confidence: Optional[List[FixConfidence]] = Query(None),
    fix_type: Optional[List[FixType]] = Query(None),
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_direction: str = "desc",
) -> PaginatedResponse:
    """List fixes with filters and pagination."""
    # Filter fixes
    fixes = list(_fixes_store.values())

    if status:
        fixes = [f for f in fixes if f.status in status]
    if confidence:
        fixes = [f for f in fixes if f.confidence in confidence]
    if fix_type:
        fixes = [f for f in fixes if f.fix_type in fix_type]
    if search:
        search_lower = search.lower()
        fixes = [
            f for f in fixes
            if search_lower in f.title.lower() or search_lower in f.description.lower()
        ]

    # Sort
    reverse = sort_direction == "desc"
    if sort_by == "created_at":
        fixes.sort(key=lambda f: f.created_at, reverse=reverse)
    elif sort_by == "confidence":
        confidence_order = {"high": 3, "medium": 2, "low": 1}
        fixes.sort(key=lambda f: confidence_order.get(f.confidence.value, 0), reverse=reverse)
    elif sort_by == "status":
        fixes.sort(key=lambda f: f.status.value, reverse=reverse)

    # Paginate
    total = len(fixes)
    start = (page - 1) * page_size
    end = start + page_size
    items = fixes[start:end]

    return PaginatedResponse(
        items=[f.to_dict() for f in items],
        total=total,
        page=page,
        page_size=page_size,
        has_more=end < total,
    )


@router.get("/{fix_id}")
async def get_fix(fix_id: str, user: ClerkUser = Depends(get_current_user)) -> dict:
    """Get a specific fix by ID."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")
    return _fixes_store[fix_id].to_dict()


@router.post("/{fix_id}/approve")
async def approve_fix(fix_id: str, user: ClerkUser = Depends(get_current_user)) -> dict:
    """Approve a fix."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix = _fixes_store[fix_id]
    if fix.status != FixStatus.PENDING:
        raise HTTPException(status_code=400, detail="Fix is not pending")

    fix.status = FixStatus.APPROVED
    return {"data": fix.to_dict(), "success": True}


@router.post("/{fix_id}/reject")
async def reject_fix(fix_id: str, request: RejectRequest, user: ClerkUser = Depends(get_current_user)) -> dict:
    """Reject a fix with a reason."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix = _fixes_store[fix_id]
    if fix.status != FixStatus.PENDING:
        raise HTTPException(status_code=400, detail="Fix is not pending")

    fix.status = FixStatus.REJECTED
    # Store rejection reason in comments
    comment_id = f"reject-{fix_id}-{datetime.utcnow().timestamp()}"
    if fix_id not in _comments_store:
        _comments_store[fix_id] = []
    _comments_store[fix_id].append({
        "id": comment_id,
        "fix_id": fix_id,
        "author": "System",
        "content": f"Rejected: {request.reason}",
        "created_at": datetime.utcnow().isoformat(),
    })

    return {"data": fix.to_dict(), "success": True}


@router.post("/{fix_id}/apply")
async def apply_fix(fix_id: str, user: ClerkUser = Depends(get_current_user)) -> dict:
    """Apply an approved fix to the codebase."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix = _fixes_store[fix_id]
    if fix.status != FixStatus.APPROVED:
        raise HTTPException(status_code=400, detail="Fix must be approved before applying")

    # TODO: Actually apply the fix using the applicator
    # For now, just mark it as applied
    fix.status = FixStatus.APPLIED
    fix.applied_at = datetime.utcnow()

    return {"data": fix.to_dict(), "success": True}


# In-memory cache for preview results
_preview_cache: dict[str, tuple[PreviewResult, str]] = {}  # fix_id -> (result, fix_hash)


def _get_fix_hash(fix: FixProposal) -> str:
    """Generate a hash for fix content to detect changes."""
    import hashlib
    content = "".join(
        f"{c.file_path}:{c.fixed_code}" for c in fix.changes
    )
    return hashlib.md5(content.encode()).hexdigest()[:16]


def _get_preview_cache():
    """Lazy import to avoid circular dependency."""
    from repotoire.cache import get_preview_cache

    return get_preview_cache


@router.post("/{fix_id}/preview")
async def preview_fix(
    fix_id: str,
    user: ClerkUser = Depends(get_current_user),
    cache: "PreviewCache" = Depends(_get_preview_cache),
) -> PreviewResult:
    """Run fix preview in sandbox to validate before approving.

    Executes the proposed fix in an isolated E2B sandbox and runs:
    - Syntax validation (ast.parse)
    - Import validation (module can be imported)
    - Optional type checking (mypy)

    Returns detailed results for each check.
    """
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix = _fixes_store[fix_id]
    fix_hash = _get_fix_hash(fix)

    # Check Redis cache first (with hash validation)
    cached_result = await cache.get_with_hash_check(fix_id, fix_hash)
    if cached_result:
        logger.info(f"Returning cached preview for fix {fix_id}")
        return cached_result

    # Fallback to in-memory cache
    if fix_id in _preview_cache:
        cached_result, cached_hash = _preview_cache[fix_id]
        if cached_hash == fix_hash:
            # Return cached result with timestamp
            logger.info(f"Returning in-memory cached preview for fix {fix_id}")
            return cached_result

    start_time = time.time()
    checks: List[PreviewCheck] = []
    stdout_parts: List[str] = []
    stderr_parts: List[str] = []

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
                    import ast
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

        # Full sandbox validation
        async with CodeValidator(validation_config, sandbox_config) as validator:
            for change in fix.changes:
                file_path = str(change.file_path)

                validation_result = await validator.validate(
                    fixed_code=change.fixed_code,
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
async def get_comments(fix_id: str, user: ClerkUser = Depends(get_current_user), limit: int = Query(25, ge=1, le=100)) -> List[dict]:
    """Get comments for a fix."""
    if fix_id not in _fixes_store:
        raise HTTPException(status_code=404, detail="Fix not found")

    comments = _comments_store.get(fix_id, [])
    return comments[:limit]


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


# Helper function to add fixes from the engine
def add_fix_to_store(fix: FixProposal) -> None:
    """Add a fix to the in-memory store."""
    _fixes_store[fix.id] = fix


def get_all_fixes() -> List[FixProposal]:
    """Get all fixes from the store."""
    return list(_fixes_store.values())


def clear_fixes_store() -> None:
    """Clear the fixes store (for testing)."""
    _fixes_store.clear()
    _comments_store.clear()


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

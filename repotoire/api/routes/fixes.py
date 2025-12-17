"""API routes for fix management and Best-of-N generation."""

from __future__ import annotations

import asyncio
import os
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
from repotoire.api.auth import ClerkUser, get_current_user
from repotoire.api.models import PreviewResult, PreviewCheck
from repotoire.db.models import PlanTier
from repotoire.db.models.fix import Fix, FixStatus, FixConfidence, FixType
from repotoire.db.models.user import User
from repotoire.db.repositories.fix import FixRepository
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from sqlalchemy import select

if TYPE_CHECKING:
    from repotoire.cache import PreviewCache

logger = get_logger(__name__)

router = APIRouter(prefix="/fixes", tags=["fixes"])

# In-memory storage for legacy FixProposal objects (Best-of-N)
_fixes_store: dict[str, FixProposal] = {}
_finding_to_fix_index: dict[str, str] = {}  # finding_id -> fix_id for O(1) lookup
_comments_store: dict[str, list] = {}

# Thread-safe locks for global state access
# Required because async handlers may access these concurrently
_fixes_lock = asyncio.Lock()
_comments_lock = asyncio.Lock()


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
    status: Optional[List[str]] = Query(None, description="Filter by status (pending, approved, rejected, applied, failed)"),
    confidence: Optional[List[str]] = Query(None, description="Filter by confidence (high, medium, low)"),
    fix_type: Optional[List[str]] = Query(None, description="Filter by fix type"),
    repository_id: Optional[str] = Query(None, description="Filter by repository UUID"),
    search: Optional[str] = Query(None, description="Search in title and description"),
    sort_by: str = Query("created_at", description="Field to sort by"),
    sort_direction: str = Query("desc", description="Sort direction: 'asc' or 'desc'"),
) -> PaginatedResponse:
    """List AI-generated fix proposals with filtering and pagination."""
    repo = FixRepository(db)

    # Convert string params to enums
    status_enums = [FixStatus(s) for s in status] if status else None
    confidence_enums = [FixConfidence(c) for c in confidence] if confidence else None
    fix_type_enums = [FixType(t) for t in fix_type] if fix_type else None
    repo_uuid = UUID(repository_id) if repository_id else None

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
        async with _comments_lock:
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

**Process:**
1. Validates fix is approved
2. Creates git branch (if enabled)
3. Applies code changes to files
4. Creates git commit (if enabled)
5. Updates fix status to `applied`

**Options:**
- `repository_path`: Where to apply changes (required for actual application)
- `create_branch`: Create a new branch for review (default: true)
- `commit`: Create a git commit (default: true)

If `repository_path` is omitted, only the status is updated (for manual application tracking).
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
        500: {"description": "Failed to apply fix"},
    },
)
async def apply_fix(
    fix_id: str,
    request: Optional[ApplyFixRequest] = None,
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

    # If repository_path provided, actually apply the fix to filesystem
    if request and request.repository_path:
        # Security: Get allowed base directory from environment (with safe default)
        allowed_base = Path(
            os.getenv("ALLOWED_REPO_BASE", str(Path.cwd()))
        ).resolve()

        # Security: Resolve user-provided path to canonical form (handles .. and symlinks)
        try:
            repository_path = Path(request.repository_path).resolve()
        except (OSError, ValueError):
            logger.warning(f"Invalid path format in apply_fix: user={user.user_id}")
            raise HTTPException(status_code=400, detail="Invalid repository path")

        # Security check 1: Path must exist
        if not repository_path.exists():
            raise HTTPException(status_code=400, detail="Repository path not found")

        # Security check 2: Reject symlinks (defense in depth - prevents symlink bypass)
        if Path(request.repository_path).is_symlink():
            logger.warning(
                f"Path traversal attempt (symlink) blocked: user={user.user_id}"
            )
            raise HTTPException(status_code=400, detail="Invalid repository path")

        # Security check 3: Must be within allowed base directory (prevents path traversal)
        try:
            repository_path.relative_to(allowed_base)
        except ValueError:
            logger.warning(
                f"Path traversal attempt blocked: user={user.user_id}, "
                f"attempted_path={request.repository_path}"
            )
            raise HTTPException(status_code=400, detail="Invalid repository path")

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

    return {"data": _fix_to_dict(fix), "success": True}


# In-memory cache for preview results
_preview_cache: dict[str, tuple[PreviewResult, str]] = {}  # fix_id -> (result, fix_hash)
_preview_cache_lock = asyncio.Lock()


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
    user: ClerkUser = Depends(get_current_user),
    cache: "PreviewCache" = Depends(_get_preview_cache),
) -> PreviewResult:
    """Run fix preview in sandbox to validate before approving."""
    async with _fixes_lock:
        fix = _fixes_store.get(fix_id)
    if fix is None:
        raise HTTPException(status_code=404, detail="Fix not found")

    fix_hash = _get_fix_hash(fix)

    # Check Redis cache first (with hash validation)
    cached_result = await cache.get_with_hash_check(fix_id, fix_hash)
    if cached_result:
        logger.info(f"Returning cached preview for fix {fix_id}")
        return cached_result

    # Fallback to in-memory cache
    async with _preview_cache_lock:
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
            async with _preview_cache_lock:
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
        async with _preview_cache_lock:
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
    async with _fixes_lock:
        fix_exists = fix_id in _fixes_store
    if not fix_exists:
        raise HTTPException(status_code=404, detail="Fix not found")

    comment_id = f"comment-{fix_id}-{datetime.utcnow().timestamp()}"
    comment = {
        "id": comment_id,
        "fix_id": fix_id,
        "author": user.user_id,
        "content": request.content,
        "created_at": datetime.utcnow().isoformat(),
    }

    async with _comments_lock:
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
    async with _fixes_lock:
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
    # Acquire locks in consistent order to prevent deadlocks
    async with _fixes_lock:
        async with _comments_lock:
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
        # Get the finding from store via index (O(1) lookup instead of O(n) scan)
        finding = None
        async with _fixes_lock:
            fix_id = _finding_to_fix_index.get(request.finding_id)
            if fix_id:
                fix = _fixes_store.get(fix_id)
                if fix:
                    finding = fix.finding

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

        # Store generated fixes and update index
        async with _fixes_lock:
            for ranked in result.ranked_fixes:
                _fixes_store[ranked.fix.id] = ranked.fix
                # Update secondary index for O(1) finding lookup
                if hasattr(ranked.fix.finding, "id") and ranked.fix.finding.id:
                    _finding_to_fix_index[ranked.fix.finding.id] = ranked.fix.id

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
    async with _fixes_lock:
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

        # Copy data for response outside the lock
        fix_data = fix.to_dict()
        rejected_count = len(related_ids)

    logger.info(
        f"Selected Best-of-N fix {fix_id}",
        extra={
            "user_id": user.user_id,
            "rejected_count": rejected_count,
        },
    )

    return {
        "data": fix_data,
        "success": True,
        "rejected_count": rejected_count,
    }


# =============================================================================
# Generate Fixes for Analysis
# =============================================================================


class GenerateFixesRequest(BaseModel):
    """Request to generate fixes for an analysis run."""

    max_fixes: int = Field(default=10, ge=1, le=50, description="Maximum number of fixes to generate")
    severity_filter: Optional[List[str]] = Field(
        default=["critical", "high"],
        description="Severities to process (critical, high, medium, low, info)"
    )


class GenerateFixesResponse(BaseModel):
    """Response from fix generation request."""

    status: str = Field(description="Task status: queued, skipped, or error")
    message: str = Field(description="Human readable message")
    task_id: Optional[str] = Field(default=None, description="Celery task ID if queued")


@router.post("/generate/{analysis_run_id}")
async def generate_fixes(
    analysis_run_id: str,
    request: GenerateFixesRequest = GenerateFixesRequest(),
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

"""API routes for analysis findings.

This module provides endpoints for retrieving code health findings
from completed analysis runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user, require_org
from repotoire.db.models import (
    AnalysisRun,
    Finding,
    FindingSeverity,
    Organization,
    Repository,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/findings", tags=["findings"])


# =============================================================================
# Response Models
# =============================================================================


class FindingResponse(BaseModel):
    """Response model for a single code health finding."""

    id: UUID = Field(..., description="Unique identifier for this finding")
    analysis_run_id: UUID = Field(..., description="Analysis run that detected this finding")
    detector: str = Field(
        ...,
        description="Detection tool that identified this issue (e.g., 'ruff', 'bandit', 'cyclomatic-complexity')",
    )
    severity: str = Field(
        ...,
        description="Severity level: critical, high, medium, low, info",
    )
    title: str = Field(..., description="Short summary of the issue")
    description: str = Field(..., description="Detailed explanation of the issue and why it matters")
    affected_files: List[str] = Field(
        default_factory=list,
        description="List of file paths affected by this finding",
    )
    affected_nodes: List[str] = Field(
        default_factory=list,
        description="Qualified names of affected code entities (e.g., 'module.Class.method')",
    )
    line_start: Optional[int] = Field(None, description="Starting line number in the primary affected file")
    line_end: Optional[int] = Field(None, description="Ending line number in the primary affected file")
    suggested_fix: Optional[str] = Field(None, description="Suggested code change to resolve this issue")
    estimated_effort: Optional[str] = Field(
        None,
        description="Estimated effort to fix: 'trivial', 'small', 'medium', 'large'",
    )
    graph_context: Optional[dict] = Field(
        None,
        description="Additional context from graph analysis (related entities, metrics)",
    )
    created_at: datetime = Field(..., description="When this finding was created")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "analysis_run_id": "660e8400-e29b-41d4-a716-446655440001",
                "detector": "cyclomatic-complexity",
                "severity": "medium",
                "title": "High cyclomatic complexity",
                "description": "Function 'process_data' has cyclomatic complexity of 15, exceeding threshold of 10.",
                "affected_files": ["src/processors/data.py"],
                "affected_nodes": ["data.py::process_data:45"],
                "line_start": 45,
                "line_end": 120,
                "suggested_fix": "Consider breaking this function into smaller, focused functions.",
                "estimated_effort": "medium",
                "graph_context": {"calls_count": 8, "callers_count": 3},
                "created_at": "2025-01-15T10:35:00Z",
            }
        },
    }


class PaginatedFindingsResponse(BaseModel):
    """Paginated response for findings list."""

    items: List[FindingResponse] = Field(..., description="List of findings for this page")
    total: int = Field(..., description="Total number of findings matching the query", ge=0)
    page: int = Field(..., description="Current page number (1-indexed)", ge=1)
    page_size: int = Field(..., description="Number of items per page", ge=1, le=100)
    has_more: bool = Field(..., description="Whether more pages are available")

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "id": "550e8400-e29b-41d4-a716-446655440000",
                        "analysis_run_id": "660e8400-e29b-41d4-a716-446655440001",
                        "detector": "bandit",
                        "severity": "high",
                        "title": "Hardcoded password",
                        "description": "Hardcoded password detected in source code.",
                        "affected_files": ["src/config.py"],
                        "affected_nodes": ["config.py::DB_PASSWORD:12"],
                        "line_start": 12,
                        "line_end": 12,
                        "suggested_fix": "Use environment variables for sensitive values.",
                        "estimated_effort": "trivial",
                        "graph_context": None,
                        "created_at": "2025-01-15T10:35:00Z",
                    }
                ],
                "total": 42,
                "page": 1,
                "page_size": 20,
                "has_more": True,
            }
        }
    }


class FindingsSummary(BaseModel):
    """Summary of findings by severity."""

    critical: int = Field(0, description="Number of critical severity findings", ge=0)
    high: int = Field(0, description="Number of high severity findings", ge=0)
    medium: int = Field(0, description="Number of medium severity findings", ge=0)
    low: int = Field(0, description="Number of low severity findings", ge=0)
    info: int = Field(0, description="Number of informational findings", ge=0)
    total: int = Field(0, description="Total number of findings across all severities", ge=0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "critical": 2,
                "high": 8,
                "medium": 15,
                "low": 12,
                "info": 5,
                "total": 42,
            }
        }
    }


class FindingsByDetector(BaseModel):
    """Findings grouped by detector."""

    detector: str = Field(..., description="Name of the detection tool")
    count: int = Field(..., description="Number of findings from this detector", ge=0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "detector": "ruff",
                "count": 25,
            }
        }
    }


# =============================================================================
# Helper Functions
# =============================================================================


async def _get_user_org(session: AsyncSession, user: ClerkUser) -> Organization | None:
    """Get user's organization."""
    if not user.org_slug:
        return None
    result = await session.execute(
        select(Organization).where(Organization.slug == user.org_slug)
    )
    return result.scalar_one_or_none()


async def _user_has_repo_access(
    session: AsyncSession,
    user: ClerkUser,
    repo: Repository,
) -> bool:
    """Check if user has access to a repository."""
    if not user.org_slug:
        return False
    org_result = await session.execute(
        select(Organization).where(Organization.slug == user.org_slug)
    )
    org = org_result.scalar_one_or_none()
    if not org:
        return False
    return repo.organization_id == org.id


async def _get_latest_analysis_run_ids(
    session: AsyncSession, org: Organization, repository_id: Optional[UUID] = None
) -> list[UUID]:
    """Get the latest completed analysis run ID for each repository in the org.

    This ensures we only count findings from the most recent analysis, not duplicates
    from multiple analysis runs on the same repo.
    """
    # Subquery to get the latest completed analysis run per repository
    subq = (
        select(
            AnalysisRun.repository_id,
            func.max(AnalysisRun.completed_at).label("max_completed")
        )
        .join(Repository, AnalysisRun.repository_id == Repository.id)
        .where(Repository.organization_id == org.id)
        .where(AnalysisRun.status == "completed")
    )

    if repository_id:
        subq = subq.where(AnalysisRun.repository_id == repository_id)

    subq = subq.group_by(AnalysisRun.repository_id).subquery()

    # Get the analysis run IDs that match the latest completed_at per repo
    query = (
        select(AnalysisRun.id)
        .join(subq,
              (AnalysisRun.repository_id == subq.c.repository_id) &
              (AnalysisRun.completed_at == subq.c.max_completed))
    )

    result = await session.execute(query)
    return [row[0] for row in result.all()]


# =============================================================================
# Endpoints
# =============================================================================


@router.get(
    "",
    response_model=PaginatedFindingsResponse,
    summary="List findings",
    description="""
List code health findings with pagination and filtering.

Returns findings from analysis runs for repositories the user has access to.

**Deduplication:**
By default, only shows findings from the latest completed analysis run per repository
to avoid showing duplicates from multiple analysis runs. Set `all_runs=true` to see
all historical findings.

**Filtering:**
- `severity`: Filter by one or more severity levels (critical, high, medium, low, info)
- `detector`: Filter by detection tool name (e.g., 'ruff', 'bandit', 'mypy')
- `repository_id`: Limit to a specific repository
- `analysis_run_id`: Limit to a specific analysis run

**Sorting:**
- `sort_by`: Field to sort by (created_at, severity, detector)
- `sort_direction`: 'asc' or 'desc'

**Pagination:**
- `page`: Page number (1-indexed)
- `page_size`: Items per page (max 100)
    """,
    responses={
        200: {"description": "Findings retrieved successfully"},
        403: {
            "description": "Organization not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "forbidden",
                        "detail": "Organization not found",
                        "error_code": "FORBIDDEN",
                    }
                }
            },
        },
    },
)
async def list_findings(
    analysis_run_id: Optional[UUID] = Query(None, description="Filter by specific analysis run ID"),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository ID"),
    severity: Optional[List[str]] = Query(
        None,
        description="Filter by severity levels (can specify multiple)",
        example=["critical", "high"],
    ),
    detector: Optional[str] = Query(None, description="Filter by detector name (e.g., 'ruff', 'bandit')"),
    all_runs: bool = Query(False, description="Include findings from all runs, not just the latest per repo"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    sort_by: str = Query("created_at", description="Field to sort by"),
    sort_direction: str = Query("desc", pattern="^(asc|desc)$", description="Sort direction: 'asc' or 'desc'"),
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> PaginatedFindingsResponse:
    """List code health findings with pagination and filtering."""
    # Get user's organization
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization not found",
        )

    # Build base query joining findings to repos via analysis runs
    query = (
        select(Finding)
        .join(AnalysisRun, Finding.analysis_run_id == AnalysisRun.id)
        .join(Repository, AnalysisRun.repository_id == Repository.id)
        .where(Repository.organization_id == org.id)
    )

    # Apply filters
    if analysis_run_id:
        # If specific run is requested, use that
        query = query.where(Finding.analysis_run_id == analysis_run_id)
    elif not all_runs:
        # Default to latest runs only to avoid duplicates
        latest_run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)
        if latest_run_ids:
            query = query.where(Finding.analysis_run_id.in_(latest_run_ids))
        else:
            # No completed runs, return empty
            return PaginatedFindingsResponse(
                items=[],
                total=0,
                page=page,
                page_size=page_size,
                has_more=False,
            )

    if repository_id:
        query = query.where(AnalysisRun.repository_id == repository_id)

    if severity:
        # Convert string severity to enum
        severity_enums = []
        for s in severity:
            try:
                severity_enums.append(FindingSeverity(s.lower()))
            except ValueError:
                pass
        if severity_enums:
            query = query.where(Finding.severity.in_(severity_enums))

    if detector:
        query = query.where(Finding.detector == detector)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # Apply sorting
    sort_column = getattr(Finding, sort_by, Finding.created_at)
    if sort_direction == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    # Execute query
    result = await session.execute(query)
    findings = result.scalars().all()

    return PaginatedFindingsResponse(
        items=[
            FindingResponse(
                id=f.id,
                analysis_run_id=f.analysis_run_id,
                detector=f.detector,
                severity=f.severity.value,
                title=f.title,
                description=f.description,
                affected_files=f.affected_files or [],
                affected_nodes=f.affected_nodes or [],
                line_start=f.line_start,
                line_end=f.line_end,
                suggested_fix=f.suggested_fix,
                estimated_effort=f.estimated_effort,
                graph_context=f.graph_context,
                created_at=f.created_at,
            )
            for f in findings
        ],
        total=total,
        page=page,
        page_size=page_size,
        has_more=(offset + page_size) < total,
    )


@router.get(
    "/summary",
    response_model=FindingsSummary,
    summary="Get findings summary",
    description="""
Get a summary count of findings grouped by severity level.

By default, only counts findings from the latest completed analysis run per repository
to provide an accurate picture of current code health.

**Use Cases:**
- Dashboard summary cards
- Health score widgets
- Trend comparison (compare with previous summary)

**Filtering:**
- `repository_id`: Limit to a specific repository
- `analysis_run_id`: Limit to a specific analysis run
    """,
    responses={
        200: {"description": "Summary retrieved successfully"},
        403: {
            "description": "Organization not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "forbidden",
                        "detail": "Organization not found",
                        "error_code": "FORBIDDEN",
                    }
                }
            },
        },
    },
)
async def get_findings_summary(
    analysis_run_id: Optional[UUID] = Query(None, description="Filter by specific analysis run ID"),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository ID"),
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> FindingsSummary:
    """Get a summary count of findings grouped by severity level."""
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization not found",
        )

    # Determine which run IDs to use
    if analysis_run_id:
        # Verify the analysis run belongs to this org before using it
        run_query = (
            select(AnalysisRun.id)
            .join(Repository, AnalysisRun.repository_id == Repository.id)
            .where(AnalysisRun.id == analysis_run_id)
            .where(Repository.organization_id == org.id)
        )
        run_result = await session.execute(run_query)
        if not run_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this analysis run",
            )
        run_ids = [analysis_run_id]
    else:
        run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)
        if not run_ids:
            return FindingsSummary()

    # Build query - only from specified/latest runs
    query = (
        select(Finding.severity, func.count(Finding.id).label("count"))
        .where(Finding.analysis_run_id.in_(run_ids))
        .group_by(Finding.severity)
    )

    result = await session.execute(query)
    rows = result.all()

    # Build summary
    summary = FindingsSummary()
    for severity, count in rows:
        setattr(summary, severity.value, count)
        summary.total += count

    return summary


@router.get(
    "/by-detector",
    response_model=List[FindingsByDetector],
    summary="Get findings by detector",
    description="""
Get findings grouped and counted by detection tool.

Returns a list of detectors sorted by finding count (descending).
Useful for understanding which types of issues are most common.

**Available Detectors:**
- `ruff` - General linting (400+ rules)
- `pylint` - Python-specific checks
- `mypy` - Type checking errors
- `bandit` - Security vulnerabilities
- `radon` - Complexity metrics
- `jscpd` - Duplicate code detection
- `vulture` - Dead code detection
- `semgrep` - Advanced security patterns

By default, only counts findings from the latest completed analysis run per repository.
    """,
    responses={
        200: {"description": "Findings by detector retrieved successfully"},
        403: {
            "description": "Organization not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "forbidden",
                        "detail": "Organization not found",
                        "error_code": "FORBIDDEN",
                    }
                }
            },
        },
    },
)
async def get_findings_by_detector(
    analysis_run_id: Optional[UUID] = Query(None, description="Filter by specific analysis run ID"),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository ID"),
    limit: int = Query(20, ge=1, le=50, description="Maximum number of detectors to return"),
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> List[FindingsByDetector]:
    """Get findings grouped and counted by detection tool."""
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization not found",
        )

    # Determine which run IDs to use
    if analysis_run_id:
        # Verify the analysis run belongs to this org before using it
        run_query = (
            select(AnalysisRun.id)
            .join(Repository, AnalysisRun.repository_id == Repository.id)
            .where(AnalysisRun.id == analysis_run_id)
            .where(Repository.organization_id == org.id)
        )
        run_result = await session.execute(run_query)
        if not run_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this analysis run",
            )
        run_ids = [analysis_run_id]
    else:
        run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)
        if not run_ids:
            return []

    query = (
        select(Finding.detector, func.count(Finding.id).label("count"))
        .where(Finding.analysis_run_id.in_(run_ids))
        .group_by(Finding.detector)
        .order_by(func.count(Finding.id).desc())
        .limit(limit)
    )

    result = await session.execute(query)
    rows = result.all()

    return [FindingsByDetector(detector=detector, count=count) for detector, count in rows]


@router.get(
    "/{finding_id}",
    response_model=FindingResponse,
    summary="Get finding details",
    description="""
Get detailed information about a single finding by ID.

Returns the full finding object including:
- Affected files and code locations
- Suggested fix (if available)
- Graph context (related entities)
- Estimated effort to fix
    """,
    responses={
        200: {"description": "Finding retrieved successfully"},
        403: {
            "description": "Access denied to this finding",
            "content": {
                "application/json": {
                    "example": {
                        "error": "forbidden",
                        "detail": "Access denied",
                        "error_code": "FORBIDDEN",
                    }
                }
            },
        },
        404: {
            "description": "Finding not found",
            "content": {
                "application/json": {
                    "example": {
                        "error": "not_found",
                        "detail": "Finding not found",
                        "error_code": "NOT_FOUND",
                    }
                }
            },
        },
    },
)
async def get_finding(
    finding_id: UUID,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> FindingResponse:
    """Get detailed information about a single finding."""
    # Get finding with access check
    query = (
        select(Finding)
        .join(AnalysisRun, Finding.analysis_run_id == AnalysisRun.id)
        .join(Repository, AnalysisRun.repository_id == Repository.id)
        .where(Finding.id == finding_id)
    )

    result = await session.execute(query)
    finding = result.scalar_one_or_none()

    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Finding not found",
        )

    # Get repository and check access
    analysis = await session.get(AnalysisRun, finding.analysis_run_id)
    repo = await session.get(Repository, analysis.repository_id)

    if not await _user_has_repo_access(session, user, repo):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return FindingResponse(
        id=finding.id,
        analysis_run_id=finding.analysis_run_id,
        detector=finding.detector,
        severity=finding.severity.value,
        title=finding.title,
        description=finding.description,
        affected_files=finding.affected_files or [],
        affected_nodes=finding.affected_nodes or [],
        line_start=finding.line_start,
        line_end=finding.line_end,
        suggested_fix=finding.suggested_fix,
        estimated_effort=finding.estimated_effort,
        graph_context=finding.graph_context,
        created_at=finding.created_at,
    )

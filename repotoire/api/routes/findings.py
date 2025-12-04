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

from repotoire.api.auth import ClerkUser, get_current_user, require_org
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
    """Response model for a single finding."""

    id: UUID
    analysis_run_id: UUID
    detector: str
    severity: str
    title: str
    description: str
    affected_files: List[str]
    affected_nodes: List[str]
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    suggested_fix: Optional[str] = None
    estimated_effort: Optional[str] = None
    graph_context: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class PaginatedFindingsResponse(BaseModel):
    """Paginated response for findings list."""

    items: List[FindingResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class FindingsSummary(BaseModel):
    """Summary of findings by severity."""

    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0
    total: int = 0


class FindingsByDetector(BaseModel):
    """Findings grouped by detector."""

    detector: str
    count: int


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


# =============================================================================
# Endpoints
# =============================================================================


@router.get("", response_model=PaginatedFindingsResponse)
async def list_findings(
    analysis_run_id: Optional[UUID] = Query(None, description="Filter by analysis run"),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
    severity: Optional[List[str]] = Query(None, description="Filter by severity"),
    detector: Optional[str] = Query(None, description="Filter by detector"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("created_at", description="Sort field"),
    sort_direction: str = Query("desc", regex="^(asc|desc)$", description="Sort direction"),
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> PaginatedFindingsResponse:
    """List findings with pagination and filtering.

    Returns findings from analysis runs for repositories the user has access to.
    """
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
        query = query.where(Finding.analysis_run_id == analysis_run_id)

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


@router.get("/summary", response_model=FindingsSummary)
async def get_findings_summary(
    analysis_run_id: Optional[UUID] = Query(None, description="Filter by analysis run"),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> FindingsSummary:
    """Get summary of findings by severity."""
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization not found",
        )

    # Build query
    query = (
        select(Finding.severity, func.count(Finding.id).label("count"))
        .join(AnalysisRun, Finding.analysis_run_id == AnalysisRun.id)
        .join(Repository, AnalysisRun.repository_id == Repository.id)
        .where(Repository.organization_id == org.id)
    )

    if analysis_run_id:
        query = query.where(Finding.analysis_run_id == analysis_run_id)

    if repository_id:
        query = query.where(AnalysisRun.repository_id == repository_id)

    query = query.group_by(Finding.severity)

    result = await session.execute(query)
    rows = result.all()

    # Build summary
    summary = FindingsSummary()
    for severity, count in rows:
        setattr(summary, severity.value, count)
        summary.total += count

    return summary


@router.get("/by-detector", response_model=List[FindingsByDetector])
async def get_findings_by_detector(
    analysis_run_id: Optional[UUID] = Query(None, description="Filter by analysis run"),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
    limit: int = Query(20, ge=1, le=50, description="Max detectors to return"),
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> List[FindingsByDetector]:
    """Get findings grouped by detector."""
    org = await _get_user_org(session, user)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization not found",
        )

    query = (
        select(Finding.detector, func.count(Finding.id).label("count"))
        .join(AnalysisRun, Finding.analysis_run_id == AnalysisRun.id)
        .join(Repository, AnalysisRun.repository_id == Repository.id)
        .where(Repository.organization_id == org.id)
    )

    if analysis_run_id:
        query = query.where(Finding.analysis_run_id == analysis_run_id)

    if repository_id:
        query = query.where(AnalysisRun.repository_id == repository_id)

    query = query.group_by(Finding.detector).order_by(func.count(Finding.id).desc()).limit(limit)

    result = await session.execute(query)
    rows = result.all()

    return [FindingsByDetector(detector=detector, count=count) for detector, count in rows]


@router.get("/{finding_id}", response_model=FindingResponse)
async def get_finding(
    finding_id: UUID,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> FindingResponse:
    """Get a single finding by ID."""
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

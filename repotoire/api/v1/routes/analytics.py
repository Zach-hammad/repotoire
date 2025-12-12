"""API routes for analytics.

Dashboard analytics based on analysis findings (code health issues detected).
"""

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
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
from repotoire.db.models.fix import Fix, FixStatus
from repotoire.db.session import get_db

router = APIRouter(prefix="/analytics", tags=["analytics"])


class AnalyticsSummary(BaseModel):
    """Dashboard analytics summary based on findings."""

    total_findings: int
    critical: int
    high: int
    medium: int
    low: int
    info: int
    by_severity: Dict[str, int]
    by_detector: Dict[str, int]


class TrendDataPoint(BaseModel):
    """A single data point for trends (findings by date)."""

    date: str
    critical: int
    high: int
    medium: int
    low: int
    info: int
    total: int


class FileHotspot(BaseModel):
    """File hotspot analysis (files with most findings)."""

    file_path: str
    finding_count: int
    severity_breakdown: Dict[str, int]


class HealthScoreResponse(BaseModel):
    """Overall health score for dashboard."""
    score: Optional[int] = None  # None indicates not analyzed
    grade: Optional[str] = None  # None indicates not analyzed
    trend: str = "unknown"  # "improving", "declining", "stable", "unknown"
    categories: Optional[Dict[str, int]] = None  # None indicates not analyzed


async def _get_user_org(session: AsyncSession, user: ClerkUser) -> Organization | None:
    """Get user's organization."""
    if not user.org_slug:
        return None
    result = await session.execute(
        select(Organization).where(Organization.slug == user.org_slug)
    )
    return result.scalar_one_or_none()


async def _get_latest_analysis_run_ids(
    session: AsyncSession, org: Organization, repository_id: Optional[UUID] = None
) -> list[UUID]:
    """Get the latest completed analysis run ID for each repository in the org.

    This ensures we only count findings from the most recent analysis, not duplicates
    from multiple analysis runs on the same repo.
    """
    from sqlalchemy import distinct
    from sqlalchemy.orm import aliased

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


@router.get("/summary")
async def get_summary(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
) -> AnalyticsSummary:
    """Get dashboard summary statistics based on analysis findings.

    Only counts findings from the latest completed analysis run per repository
    to avoid duplicating counts when re-running analysis.
    """
    org = await _get_user_org(session, user)
    if not org:
        return AnalyticsSummary(
            total_findings=0,
            critical=0,
            high=0,
            medium=0,
            low=0,
            info=0,
            by_severity={},
            by_detector={},
        )

    # Get latest analysis run IDs to avoid counting duplicates
    latest_run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)

    if not latest_run_ids:
        return AnalyticsSummary(
            total_findings=0,
            critical=0,
            high=0,
            medium=0,
            low=0,
            info=0,
            by_severity={},
            by_detector={},
        )

    # Build base query for severity counts - only from latest runs
    severity_query = (
        select(Finding.severity, func.count(Finding.id).label("count"))
        .where(Finding.analysis_run_id.in_(latest_run_ids))
        .group_by(Finding.severity)
    )
    severity_result = await session.execute(severity_query)
    severity_rows = severity_result.all()

    # Build severity counts
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    total = 0
    for severity, count in severity_rows:
        severity_counts[severity.value] = count
        total += count

    # Build detector query - only from latest runs
    detector_query = (
        select(Finding.detector, func.count(Finding.id).label("count"))
        .where(Finding.analysis_run_id.in_(latest_run_ids))
        .group_by(Finding.detector)
    )
    detector_result = await session.execute(detector_query)
    detector_rows = detector_result.all()

    detector_counts = {detector: count for detector, count in detector_rows}

    return AnalyticsSummary(
        total_findings=total,
        critical=severity_counts["critical"],
        high=severity_counts["high"],
        medium=severity_counts["medium"],
        low=severity_counts["low"],
        info=severity_counts["info"],
        by_severity=severity_counts,
        by_detector=detector_counts,
    )


@router.get("/trends")
async def get_trends(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
    period: str = Query("week", regex="^(day|week|month)$"),
    limit: int = Query(30, ge=1, le=90),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
) -> List[TrendDataPoint]:
    """Get cumulative trend data for charts based on findings.

    Shows the running total of findings from the latest analysis run per repo,
    as of each day. This provides a meaningful trend line that doesn't drop to 0
    on days without new analysis runs.
    """
    org = await _get_user_org(session, user)
    if not org:
        return []

    # Get the current total findings from latest analysis runs (same as summary)
    latest_run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)

    if not latest_run_ids:
        # No analysis runs - return empty trend with 0s
        today = datetime.utcnow().date()
        trends = []
        for i in range(limit - 1, -1, -1):
            date = today - timedelta(days=i)
            trends.append(
                TrendDataPoint(
                    date=date.isoformat(),
                    critical=0,
                    high=0,
                    medium=0,
                    low=0,
                    info=0,
                    total=0,
                )
            )
        return trends

    # Get the current severity counts from latest runs
    severity_query = (
        select(Finding.severity, func.count(Finding.id).label("count"))
        .where(Finding.analysis_run_id.in_(latest_run_ids))
        .group_by(Finding.severity)
    )
    severity_result = await session.execute(severity_query)
    severity_rows = severity_result.all()

    current_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for severity, count in severity_rows:
        current_counts[severity.value] = count

    # Get historical analysis runs to track when findings changed
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=limit)

    # Query all completed analysis runs in the time period with their finding counts
    history_query = (
        select(
            func.date(AnalysisRun.completed_at).label("date"),
            Finding.severity,
            func.count(Finding.id).label("count"),
        )
        .join(Finding, Finding.analysis_run_id == AnalysisRun.id)
        .join(Repository, AnalysisRun.repository_id == Repository.id)
        .where(Repository.organization_id == org.id)
        .where(AnalysisRun.status == "completed")
        .where(AnalysisRun.completed_at >= start_date)
    )

    if repository_id:
        history_query = history_query.where(AnalysisRun.repository_id == repository_id)

    history_query = history_query.group_by(func.date(AnalysisRun.completed_at), Finding.severity)
    result = await session.execute(history_query)
    rows = result.all()

    # Build lookup of counts by date when analysis ran
    date_counts: Dict[str, Dict[str, int]] = {}
    for date_val, severity, count in rows:
        date_str = date_val.isoformat() if hasattr(date_val, "isoformat") else str(date_val)
        if date_str not in date_counts:
            date_counts[date_str] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        date_counts[date_str][severity.value] = count

    # Generate trend data - use current counts and show them consistently
    # For a cleaner chart, show the latest known counts for each day
    # (findings persist until next analysis replaces them)
    trends = []
    last_known_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

    for i in range(limit - 1, -1, -1):
        date = today - timedelta(days=i)
        date_str = date.isoformat()

        # If there was an analysis on this day, update the counts
        if date_str in date_counts:
            last_known_counts = date_counts[date_str].copy()

        total = sum(last_known_counts.values())
        trends.append(
            TrendDataPoint(
                date=date_str,
                critical=last_known_counts["critical"],
                high=last_known_counts["high"],
                medium=last_known_counts["medium"],
                low=last_known_counts["low"],
                info=last_known_counts["info"],
                total=total,
            )
        )

    return trends


@router.get("/by-type")
async def get_by_type(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
) -> Dict[str, int]:
    """Get finding counts by detector type.

    Only counts findings from the latest completed analysis run per repository.
    """
    org = await _get_user_org(session, user)
    if not org:
        return {}

    # Get latest analysis run IDs to avoid counting duplicates
    latest_run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)
    if not latest_run_ids:
        return {}

    query = (
        select(Finding.detector, func.count(Finding.id).label("count"))
        .where(Finding.analysis_run_id.in_(latest_run_ids))
        .group_by(Finding.detector)
    )
    result = await session.execute(query)
    rows = result.all()

    return {detector: count for detector, count in rows}


@router.get("/by-file")
async def get_by_file(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
    limit: int = Query(10, ge=1, le=50),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
) -> List[FileHotspot]:
    """Get file hotspot analysis based on findings.

    Only counts findings from the latest completed analysis run per repository.
    """
    org = await _get_user_org(session, user)
    if not org:
        return []

    # Get latest analysis run IDs to avoid counting duplicates
    latest_run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)
    if not latest_run_ids:
        return []

    # Query findings with file paths (from affected_files array)
    query = (
        select(Finding)
        .where(Finding.analysis_run_id.in_(latest_run_ids))
    )

    result = await session.execute(query)
    findings = result.scalars().all()

    # Count findings per file
    file_counts: Dict[str, Dict[str, Any]] = {}
    for finding in findings:
        affected_files = finding.affected_files or []
        for file_path in affected_files:
            if file_path not in file_counts:
                file_counts[file_path] = {
                    "count": 0,
                    "severities": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0},
                }
            file_counts[file_path]["count"] += 1
            severity = finding.severity.value if finding.severity else "medium"
            if severity in file_counts[file_path]["severities"]:
                file_counts[file_path]["severities"][severity] += 1

    # Sort by count descending and limit
    sorted_files = sorted(file_counts.items(), key=lambda x: x[1]["count"], reverse=True)[:limit]

    return [
        FileHotspot(
            file_path=file_path,
            finding_count=data["count"],
            severity_breakdown=data["severities"],
        )
        for file_path, data in sorted_files
    ]


def _calculate_grade(score: int) -> str:
    """Calculate letter grade from score."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    return "F"


class RepositoryInfo(BaseModel):
    """Repository info for filter dropdowns."""

    id: UUID
    full_name: str
    health_score: Optional[int]
    last_analyzed_at: Optional[datetime]


@router.get("/repositories")
async def get_repositories(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
) -> List[RepositoryInfo]:
    """Get all repositories for the organization.

    Used for populating filter dropdowns on findings/fixes pages.
    """
    org = await _get_user_org(session, user)
    if not org:
        return []

    query = (
        select(Repository)
        .where(Repository.organization_id == org.id)
        .where(Repository.is_active == True)
        .order_by(Repository.full_name)
    )
    result = await session.execute(query)
    repos = result.scalars().all()

    return [
        RepositoryInfo(
            id=repo.id,
            full_name=repo.full_name,
            health_score=repo.health_score,
            last_analyzed_at=repo.last_analyzed_at,
        )
        for repo in repos
    ]


@router.get("/health-score")
async def get_health_score(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
) -> HealthScoreResponse:
    """Get overall health score for dashboard.

    If a repository_id is provided, returns the health score from the latest
    analysis run for that repository. Otherwise returns a default score.
    """
    org = await _get_user_org(session, user)
    if not org:
        # No org - return "not analyzed" state
        return HealthScoreResponse()

    # Get the latest analysis run for the repository (or org-wide)
    query = (
        select(AnalysisRun)
        .join(Repository, AnalysisRun.repository_id == Repository.id)
        .where(Repository.organization_id == org.id)
        .where(AnalysisRun.status == "completed")
    )

    if repository_id:
        query = query.where(AnalysisRun.repository_id == repository_id)

    query = query.order_by(AnalysisRun.completed_at.desc()).limit(1)
    result = await session.execute(query)
    latest_run = result.scalar_one_or_none()

    if not latest_run or latest_run.health_score is None:
        # No analysis data - return "not analyzed" state instead of fake 100/A
        return HealthScoreResponse()

    score = int(latest_run.health_score)
    grade = _calculate_grade(score)

    # Get category scores from the analysis run
    categories = {
        "structure": int(latest_run.structure_score or 100),
        "quality": int(latest_run.quality_score or 100),
        "architecture": int(latest_run.architecture_score or 100),
    }

    # Determine trend by comparing with previous analysis
    prev_query = (
        select(AnalysisRun)
        .join(Repository, AnalysisRun.repository_id == Repository.id)
        .where(Repository.organization_id == org.id)
        .where(AnalysisRun.status == "completed")
        .where(AnalysisRun.id != latest_run.id)
    )

    if repository_id:
        prev_query = prev_query.where(AnalysisRun.repository_id == repository_id)

    prev_query = prev_query.order_by(AnalysisRun.completed_at.desc()).limit(1)
    prev_result = await session.execute(prev_query)
    prev_run = prev_result.scalar_one_or_none()

    if prev_run and prev_run.health_score is not None:
        if score > prev_run.health_score:
            trend = "improving"
        elif score < prev_run.health_score:
            trend = "declining"
        else:
            trend = "stable"
    else:
        trend = "stable"

    return HealthScoreResponse(
        score=score,
        grade=grade,
        trend=trend,
        categories=categories,
    )


class FixStatistics(BaseModel):
    """Fix statistics for dashboard."""

    total: int
    pending: int
    approved: int
    applied: int
    rejected: int
    failed: int
    by_status: Dict[str, int]


@router.get("/fix-stats")
async def get_fix_statistics(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
) -> FixStatistics:
    """Get fix statistics for the dashboard.

    Returns counts of fixes by status (pending, approved, applied, rejected, failed).
    Only counts fixes from the latest completed analysis run per repository
    to avoid duplicating counts when re-running analysis.
    """
    org = await _get_user_org(session, user)
    if not org:
        return FixStatistics(
            total=0,
            pending=0,
            approved=0,
            applied=0,
            rejected=0,
            failed=0,
            by_status={},
        )

    # Get latest analysis run IDs to avoid counting duplicates
    latest_run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)
    if not latest_run_ids:
        return FixStatistics(
            total=0,
            pending=0,
            approved=0,
            applied=0,
            rejected=0,
            failed=0,
            by_status={},
        )

    # Build query for fix counts by status - only from latest runs
    query = (
        select(Fix.status, func.count(Fix.id).label("count"))
        .where(Fix.analysis_run_id.in_(latest_run_ids))
        .group_by(Fix.status)
    )
    result = await session.execute(query)
    rows = result.all()

    # Build status counts
    status_counts = {
        "pending": 0,
        "approved": 0,
        "applied": 0,
        "rejected": 0,
        "failed": 0,
    }
    total = 0
    for status, count in rows:
        status_counts[status.value] = count
        total += count

    return FixStatistics(
        total=total,
        pending=status_counts["pending"],
        approved=status_counts["approved"],
        applied=status_counts["applied"],
        rejected=status_counts["rejected"],
        failed=status_counts["failed"],
        by_status=status_counts,
    )

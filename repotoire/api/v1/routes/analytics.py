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

from repotoire.api.shared.auth import ClerkUser, get_current_user, get_current_user_or_api_key, require_org
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
    """Get user's organization by Clerk org ID."""
    if not user.org_id:
        return None
    result = await session.execute(
        select(Organization).where(Organization.clerk_org_id == user.org_id)
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
    default_branch: str
    health_score: Optional[int]
    last_analyzed_at: Optional[datetime]


@router.get("/repositories")
async def get_repositories(
    user: ClerkUser = Depends(get_current_user_or_api_key),
    session: AsyncSession = Depends(get_db),
) -> List[RepositoryInfo]:
    """Get all repositories for the organization.

    Used for populating filter dropdowns on findings/fixes pages.
    Supports both JWT and API key authentication.
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
            default_branch=repo.default_branch,
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
        "issues": int(latest_run.issues_score or 100),
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


# ==========================================
# 3D Topology Data Endpoints
# ==========================================


class TopologyNode(BaseModel):
    """A node in the code topology graph (file/module)."""

    id: str
    name: str
    path: str
    type: str  # 'file', 'module', 'class', 'function'
    size: int = 1  # For visualization sizing (e.g., line count, complexity)
    color: str = "neutral"  # 'healthy', 'warning', 'critical', 'neutral'
    findings_count: int = 0
    health_score: Optional[int] = None
    x: Optional[float] = None  # Pre-calculated position
    y: Optional[float] = None
    z: Optional[float] = None


class TopologyEdge(BaseModel):
    """An edge/connection in the code topology graph."""

    source: str  # Node ID
    target: str  # Node ID
    type: str  # 'imports', 'calls', 'inherits', 'contains'
    weight: float = 1.0  # Connection strength


class TopologyData(BaseModel):
    """Complete topology data for 3D visualization."""

    nodes: List[TopologyNode]
    edges: List[TopologyEdge]
    summary: Dict[str, Any]


@router.get("/topology")
async def get_topology_data(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
    depth: int = Query(2, ge=1, le=4, description="Depth of the topology tree"),
    limit: int = Query(100, ge=10, le=500, description="Max number of nodes"),
) -> TopologyData:
    """Get code topology data for 3D visualization.

    Returns nodes (files/modules) and edges (dependencies/imports) for
    the CodeTopologyMap 3D component.
    """
    import math
    import random

    org = await _get_user_org(session, user)
    if not org:
        return TopologyData(nodes=[], edges=[], summary={"total_nodes": 0, "total_edges": 0})

    # Get latest analysis run IDs
    latest_run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)
    if not latest_run_ids:
        return TopologyData(nodes=[], edges=[], summary={"total_nodes": 0, "total_edges": 0})

    # Get findings grouped by file to build nodes
    findings_query = (
        select(
            Finding.file_path,
            Finding.severity,
            func.count(Finding.id).label("count"),
        )
        .where(Finding.analysis_run_id.in_(latest_run_ids))
        .group_by(Finding.file_path, Finding.severity)
    )
    findings_result = await session.execute(findings_query)
    findings_rows = findings_result.all()

    # Aggregate findings per file
    file_findings: Dict[str, Dict[str, int]] = {}
    for file_path, severity, count in findings_rows:
        if file_path not in file_findings:
            file_findings[file_path] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0, "total": 0}
        file_findings[file_path][severity.value] = count
        file_findings[file_path]["total"] += count

    # Build nodes from files with findings
    nodes: List[TopologyNode] = []
    node_ids: set = set()

    # Calculate positions in a 3D sphere layout
    def spherical_position(index: int, total: int, radius: float = 5.0):
        """Generate a position on a sphere for the given index."""
        if total <= 1:
            return (0.0, 0.0, 0.0)

        # Golden ratio spiral for even distribution
        phi = math.pi * (3.0 - math.sqrt(5.0))  # Golden angle
        y = 1 - (index / float(total - 1)) * 2  # y goes from 1 to -1
        radius_at_y = math.sqrt(1 - y * y)  # radius at y

        theta = phi * index  # Golden angle increment

        x = math.cos(theta) * radius_at_y
        z = math.sin(theta) * radius_at_y

        return (x * radius, y * radius, z * radius)

    # Sort files by total findings (most problematic first)
    sorted_files = sorted(
        file_findings.items(),
        key=lambda x: x[1]["total"],
        reverse=True
    )[:limit]

    for idx, (file_path, counts) in enumerate(sorted_files):
        # Determine color based on severity
        if counts["critical"] > 0:
            color = "critical"
        elif counts["high"] > 0:
            color = "warning"
        elif counts["medium"] > 0:
            color = "warning"
        else:
            color = "healthy"

        # Calculate a health score for the file (simplified)
        total = counts["total"]
        health = max(0, 100 - (counts["critical"] * 20 + counts["high"] * 10 + counts["medium"] * 5 + counts["low"] * 2))

        # Extract file name and module path
        path_parts = file_path.rsplit("/", 1)
        name = path_parts[-1] if len(path_parts) > 1 else file_path

        # Calculate position
        x, y, z = spherical_position(idx, len(sorted_files))

        node = TopologyNode(
            id=file_path,
            name=name,
            path=file_path,
            type="file",
            size=max(1, total * 2),  # Size based on findings
            color=color,
            findings_count=total,
            health_score=health,
            x=x,
            y=y,
            z=z,
        )
        nodes.append(node)
        node_ids.add(file_path)

    # Build edges based on file proximity (simplified - could use actual import graph)
    edges: List[TopologyEdge] = []

    # Group files by directory to create implicit connections
    dir_files: Dict[str, List[str]] = {}
    for file_path in node_ids:
        dir_path = file_path.rsplit("/", 1)[0] if "/" in file_path else ""
        if dir_path not in dir_files:
            dir_files[dir_path] = []
        dir_files[dir_path].append(file_path)

    # Create edges between files in the same directory
    for dir_path, files in dir_files.items():
        for i, source in enumerate(files):
            for target in files[i + 1:]:
                # Only create edges between files in the same directory
                edges.append(
                    TopologyEdge(
                        source=source,
                        target=target,
                        type="contains",
                        weight=0.5,
                    )
                )

    # Create cross-directory edges for files with similar names (potential dependencies)
    all_files = list(node_ids)
    for i, source in enumerate(all_files):
        source_name = source.rsplit("/", 1)[-1].replace(".py", "").replace(".ts", "").replace(".js", "")
        for target in all_files[i + 1:]:
            target_name = target.rsplit("/", 1)[-1].replace(".py", "").replace(".ts", "").replace(".js", "")
            # If file names are related (e.g., "auth" in "auth_service")
            if source_name in target_name or target_name in source_name:
                if source_name != target_name:  # Not the same file
                    edges.append(
                        TopologyEdge(
                            source=source,
                            target=target,
                            type="imports",
                            weight=0.8,
                        )
                    )

    summary = {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "critical_files": len([n for n in nodes if n.color == "critical"]),
        "warning_files": len([n for n in nodes if n.color == "warning"]),
        "healthy_files": len([n for n in nodes if n.color == "healthy"]),
    }

    return TopologyData(nodes=nodes, edges=edges, summary=summary)


class HotspotTerrainData(BaseModel):
    """Hotspot data for 3D terrain visualization."""

    points: List[Dict[str, Any]]  # x, z, height (severity), color
    summary: Dict[str, Any]


@router.get("/hotspots-terrain")
async def get_hotspots_terrain(
    user: ClerkUser = Depends(require_org),
    session: AsyncSession = Depends(get_db),
    repository_id: Optional[UUID] = Query(None, description="Filter by repository"),
    limit: int = Query(50, ge=10, le=200, description="Max number of hotspots"),
) -> HotspotTerrainData:
    """Get hotspot data formatted for 3D terrain visualization.

    Returns file hotspots as points with x/z position and height based on severity.
    """
    import math

    org = await _get_user_org(session, user)
    if not org:
        return HotspotTerrainData(points=[], summary={"total": 0})

    # Get latest analysis run IDs
    latest_run_ids = await _get_latest_analysis_run_ids(session, org, repository_id)
    if not latest_run_ids:
        return HotspotTerrainData(points=[], summary={"total": 0})

    # Get hotspots (files with most findings)
    hotspots_query = (
        select(
            Finding.file_path,
            func.count(Finding.id).label("total_count"),
            func.sum(
                func.case(
                    (Finding.severity == FindingSeverity.critical, 5),
                    (Finding.severity == FindingSeverity.high, 3),
                    (Finding.severity == FindingSeverity.medium, 2),
                    (Finding.severity == FindingSeverity.low, 1),
                    else_=0,
                )
            ).label("weighted_score"),
        )
        .where(Finding.analysis_run_id.in_(latest_run_ids))
        .group_by(Finding.file_path)
        .order_by(func.count(Finding.id).desc())
        .limit(limit)
    )
    result = await session.execute(hotspots_query)
    rows = result.all()

    # Convert to terrain points
    points = []
    grid_size = int(math.ceil(math.sqrt(len(rows))))

    for idx, (file_path, count, weighted) in enumerate(rows):
        # Grid position
        row = idx // grid_size
        col = idx % grid_size

        # Normalize position to -5 to 5 range
        x = (col - grid_size / 2) * 1.5
        z = (row - grid_size / 2) * 1.5

        # Height based on weighted score (normalized)
        max_weighted = rows[0][2] if rows else 1
        height = (weighted / max_weighted) * 3.0 if max_weighted > 0 else 0.5

        # Color based on severity distribution
        if weighted and weighted > max_weighted * 0.7:
            color = "critical"
        elif weighted and weighted > max_weighted * 0.4:
            color = "warning"
        else:
            color = "healthy"

        points.append({
            "file_path": file_path,
            "name": file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path,
            "x": x,
            "z": z,
            "height": height,
            "color": color,
            "count": count,
            "weighted_score": weighted or 0,
        })

    summary = {
        "total": len(points),
        "max_count": rows[0][1] if rows else 0,
        "max_weighted": rows[0][2] if rows else 0,
    }

    return HotspotTerrainData(points=points, summary=summary)

"""API routes for analytics."""

from datetime import datetime, timedelta
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from repotoire.autofix.models import FixStatus, FixConfidence, FixType
from repotoire.api.routes.fixes import get_all_fixes
from repotoire.api.auth import ClerkUser, get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])


class AnalyticsSummary(BaseModel):
    """Dashboard analytics summary."""
    total_fixes: int
    pending: int
    approved: int
    rejected: int
    applied: int
    failed: int
    approval_rate: float
    avg_confidence: float
    by_type: Dict[str, int]
    by_confidence: Dict[str, int]


class TrendDataPoint(BaseModel):
    """A single data point for trends."""
    date: str
    pending: int
    approved: int
    rejected: int
    applied: int


class FileHotspot(BaseModel):
    """File hotspot analysis."""
    file_path: str
    fix_count: int
    severity_breakdown: Dict[str, int]


@router.get("/summary")
async def get_summary(user: ClerkUser = Depends(get_current_user)) -> AnalyticsSummary:
    """Get dashboard summary statistics."""
    fixes = get_all_fixes()

    # Count by status
    status_counts = {s.value: 0 for s in FixStatus}
    for fix in fixes:
        status_counts[fix.status.value] += 1

    # Count by type
    type_counts = {t.value: 0 for t in FixType}
    for fix in fixes:
        type_counts[fix.fix_type.value] += 1

    # Count by confidence
    confidence_counts = {c.value: 0 for c in FixConfidence}
    confidence_values = {"high": 0.95, "medium": 0.80, "low": 0.60}
    total_confidence = 0.0
    for fix in fixes:
        confidence_counts[fix.confidence.value] += 1
        total_confidence += confidence_values.get(fix.confidence.value, 0.70)

    # Calculate rates
    reviewed = status_counts["approved"] + status_counts["rejected"]
    approval_rate = (
        status_counts["approved"] / reviewed if reviewed > 0 else 0.0
    )
    avg_confidence = total_confidence / len(fixes) if fixes else 0.0

    return AnalyticsSummary(
        total_fixes=len(fixes),
        pending=status_counts["pending"],
        approved=status_counts["approved"],
        rejected=status_counts["rejected"],
        applied=status_counts["applied"],
        failed=status_counts["failed"],
        approval_rate=approval_rate,
        avg_confidence=avg_confidence,
        by_type=type_counts,
        by_confidence=confidence_counts,
    )


@router.get("/trends")
async def get_trends(
    user: ClerkUser = Depends(get_current_user),
    period: str = Query("week", regex="^(day|week|month)$"),
    limit: int = Query(30, ge=1, le=90),
) -> List[TrendDataPoint]:
    """Get trend data for charts."""
    fixes = get_all_fixes()

    # Determine date range
    today = datetime.utcnow().date()
    if period == "day":
        delta = timedelta(days=1)
    elif period == "week":
        delta = timedelta(days=7)
    else:
        delta = timedelta(days=30)

    # Group fixes by date
    trends = []
    for i in range(limit - 1, -1, -1):
        date = today - timedelta(days=i)
        date_str = date.isoformat()

        # Count fixes created on this date by status
        day_fixes = [
            f for f in fixes
            if f.created_at.date() == date
        ]

        trends.append(TrendDataPoint(
            date=date_str,
            pending=sum(1 for f in day_fixes if f.status == FixStatus.PENDING),
            approved=sum(1 for f in day_fixes if f.status == FixStatus.APPROVED),
            rejected=sum(1 for f in day_fixes if f.status == FixStatus.REJECTED),
            applied=sum(1 for f in day_fixes if f.status == FixStatus.APPLIED),
        ))

    return trends


@router.get("/by-type")
async def get_by_type(user: ClerkUser = Depends(get_current_user)) -> Dict[str, int]:
    """Get fix counts by type."""
    fixes = get_all_fixes()

    type_counts = {t.value: 0 for t in FixType}
    for fix in fixes:
        type_counts[fix.fix_type.value] += 1

    return type_counts


@router.get("/by-file")
async def get_by_file(user: ClerkUser = Depends(get_current_user), limit: int = Query(10, ge=1, le=50)) -> List[FileHotspot]:
    """Get file hotspot analysis."""
    fixes = get_all_fixes()

    # Count fixes per file
    file_counts: Dict[str, Dict[str, Any]] = {}
    for fix in fixes:
        for change in fix.changes:
            file_path = str(change.file_path)
            if file_path not in file_counts:
                file_counts[file_path] = {
                    "count": 0,
                    "severities": {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
                }
            file_counts[file_path]["count"] += 1
            # Map confidence to severity for breakdown
            severity_map = {"high": "high", "medium": "medium", "low": "low"}
            severity = severity_map.get(fix.confidence.value, "medium")
            file_counts[file_path]["severities"][severity] += 1

    # Sort and limit
    sorted_files = sorted(
        file_counts.items(),
        key=lambda x: x[1]["count"],
        reverse=True
    )[:limit]

    return [
        FileHotspot(
            file_path=file_path,
            fix_count=data["count"],
            severity_breakdown=data["severities"],
        )
        for file_path, data in sorted_files
    ]

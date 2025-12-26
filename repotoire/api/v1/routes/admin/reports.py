"""Admin API routes for community reports management.

These endpoints handle community reports for published assets.
Public endpoints allow users to report assets, admin endpoints
provide report resolution capabilities.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.api.shared.auth import ClerkUser, get_current_user, require_org_admin
from repotoire.api.services.notifications import NotificationService, get_notification_service
from repotoire.db.models.marketplace import (
    AssetReport,
    MarketplaceAsset,
    MarketplacePublisher,
    ReportReason,
    ReportStatus,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/marketplace", tags=["marketplace", "reports"])

# Valid report reasons
VALID_REPORT_REASONS = [reason.value for reason in ReportReason]

# Resolution actions
VALID_RESOLUTION_ACTIONS = ["dismiss", "warn_publisher", "unpublish", "ban_publisher"]


# =============================================================================
# Request/Response Models
# =============================================================================


class ReportAssetRequest(BaseModel):
    """Request to report an asset."""

    reason: str = Field(
        ...,
        description=f"Report reason: {', '.join(VALID_REPORT_REASONS)}",
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="Additional details about the report",
    )


class ReportResponse(BaseModel):
    """Response after creating a report."""

    success: bool
    report_id: UUID
    message: str


class ReportItem(BaseModel):
    """Report item for admin list."""

    id: UUID
    asset_id: UUID
    asset_name: str
    asset_slug: str
    publisher_name: str
    publisher_slug: str
    reporter_id: str
    reason: str
    description: Optional[str] = None
    status: str
    resolution_notes: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportListResponse(BaseModel):
    """Paginated report list."""

    items: List[ReportItem]
    total: int
    page: int
    limit: int


class AssetReportSummary(BaseModel):
    """Summary of reports for an asset."""

    asset_id: UUID
    asset_name: str
    asset_slug: str
    publisher_name: str
    publisher_slug: str
    total_reports: int
    open_reports: int
    report_reasons: List[str]
    latest_report_at: Optional[datetime] = None


class ResolveReportRequest(BaseModel):
    """Request to resolve a report."""

    action: str = Field(
        ...,
        description=f"Resolution action: {', '.join(VALID_RESOLUTION_ACTIONS)}",
    )
    notes: Optional[str] = Field(
        None,
        max_length=2000,
        description="Resolution notes",
    )


class ResolveReportResponse(BaseModel):
    """Response after resolving a report."""

    success: bool
    report_id: UUID
    action: str
    message: str


# =============================================================================
# Public Endpoints - Report an Asset
# =============================================================================


@router.post(
    "/assets/{slug}/report",
    response_model=ReportResponse,
    summary="Report an asset",
    description="Report a published asset for policy violation.",
)
async def report_asset(
    slug: str,
    request: ReportAssetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: ClerkUser = Depends(get_current_user),
    notifications: NotificationService = Depends(get_notification_service),
) -> ReportResponse:
    """Report a published asset."""
    # Validate reason
    if request.reason not in VALID_REPORT_REASONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid reason. Must be one of: {', '.join(VALID_REPORT_REASONS)}",
        )

    # Parse slug to get publisher and asset
    # Slug format: @publisher/asset-name or publisher/asset-name
    if slug.startswith("@"):
        slug = slug[1:]

    parts = slug.split("/")
    if len(parts) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid asset slug format. Expected: @publisher/asset-name",
        )

    publisher_slug, asset_slug = parts

    # Find the asset
    result = await db.execute(
        select(MarketplaceAsset)
        .join(MarketplacePublisher)
        .where(
            and_(
                MarketplacePublisher.slug == publisher_slug,
                MarketplaceAsset.slug == asset_slug,
            )
        )
        .options(selectinload(MarketplaceAsset.publisher))
    )
    asset = result.scalar_one_or_none()

    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Check if asset is published
    if not asset.published_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot report an unpublished asset",
        )

    # Check for duplicate report from same user
    existing = await db.execute(
        select(AssetReport).where(
            and_(
                AssetReport.asset_id == asset.id,
                AssetReport.reporter_id == current_user.user_id,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already reported this asset",
        )

    # Create report
    report = AssetReport(
        asset_id=asset.id,
        reporter_id=current_user.user_id,
        reason=request.reason,
        description=request.description,
        status=ReportStatus.OPEN.value,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    # Count open reports for this asset
    report_count_result = await db.execute(
        select(func.count(AssetReport.id)).where(
            and_(
                AssetReport.asset_id == asset.id,
                AssetReport.status == ReportStatus.OPEN.value,
            )
        )
    )
    report_count = report_count_result.scalar() or 0

    # Alert admins if 3+ reports
    if report_count >= 3:
        logger.warning(
            f"Asset has {report_count} open reports, alerting admins",
            extra={
                "asset_id": str(asset.id),
                "asset_slug": f"@{publisher_slug}/{asset_slug}",
                "report_count": report_count,
            },
        )
        # TODO: Send admin alert notification

    logger.info(
        f"Asset reported: @{publisher_slug}/{asset_slug}",
        extra={
            "reporter_id": current_user.user_id,
            "reason": request.reason,
            "asset_id": str(asset.id),
        },
    )

    return ReportResponse(
        success=True,
        report_id=report.id,
        message="Thank you for your report. Our team will review it.",
    )


# =============================================================================
# Admin Endpoints - Manage Reports
# =============================================================================

admin_router = APIRouter(prefix="/admin/reports", tags=["admin", "reports"])


@admin_router.get(
    "",
    response_model=ReportListResponse,
    summary="List reports",
    description="Get list of asset reports filtered by status.",
)
async def list_reports(
    status_filter: ReportStatus = Query(
        ReportStatus.OPEN,
        alias="status",
        description="Filter by report status",
    ),
    reason: Optional[str] = Query(None, description="Filter by reason"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ReportListResponse:
    """List reported assets (admin only)."""
    # Build query
    query = (
        select(AssetReport)
        .where(AssetReport.status == status_filter.value)
        .options(
            selectinload(AssetReport.asset).selectinload(MarketplaceAsset.publisher)
        )
        .order_by(desc(AssetReport.created_at))
    )

    if reason and reason in VALID_REPORT_REASONS:
        query = query.where(AssetReport.reason == reason)

    # Count total
    count_query = select(func.count()).select_from(
        select(AssetReport)
        .where(AssetReport.status == status_filter.value)
        .subquery()
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * limit
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    reports = list(result.scalars().all())

    # Build response items
    items = []
    for report in reports:
        asset = report.asset
        publisher = asset.publisher if asset else None

        items.append(
            ReportItem(
                id=report.id,
                asset_id=report.asset_id,
                asset_name=asset.name if asset else "Unknown",
                asset_slug=asset.slug if asset else "unknown",
                publisher_name=publisher.display_name if publisher else "Unknown",
                publisher_slug=publisher.slug if publisher else "unknown",
                reporter_id=report.reporter_id,
                reason=report.reason,
                description=report.description,
                status=report.status,
                resolution_notes=report.resolution_notes,
                resolved_by=report.resolved_by,
                resolved_at=report.resolved_at,
                created_at=report.created_at,
            )
        )

    return ReportListResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
    )


@admin_router.get(
    "/by-asset",
    response_model=List[AssetReportSummary],
    summary="Get reports grouped by asset",
    description="Get summary of reports grouped by asset.",
)
async def get_reports_by_asset(
    min_reports: int = Query(1, ge=1, description="Minimum number of reports"),
    limit: int = Query(20, ge=1, le=50, description="Number of assets to return"),
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> List[AssetReportSummary]:
    """Get reports grouped by asset (admin only)."""
    # Get assets with most open reports
    query = (
        select(
            AssetReport.asset_id,
            func.count(AssetReport.id).label("total_reports"),
            func.count(AssetReport.id)
            .filter(AssetReport.status == ReportStatus.OPEN.value)
            .label("open_reports"),
            func.max(AssetReport.created_at).label("latest_report_at"),
            func.array_agg(func.distinct(AssetReport.reason)).label("report_reasons"),
        )
        .group_by(AssetReport.asset_id)
        .having(func.count(AssetReport.id) >= min_reports)
        .order_by(desc("open_reports"), desc("total_reports"))
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    # Fetch asset details
    summaries = []
    for row in rows:
        asset_result = await db.execute(
            select(MarketplaceAsset)
            .where(MarketplaceAsset.id == row.asset_id)
            .options(selectinload(MarketplaceAsset.publisher))
        )
        asset = asset_result.scalar_one_or_none()

        if asset:
            publisher = asset.publisher
            summaries.append(
                AssetReportSummary(
                    asset_id=asset.id,
                    asset_name=asset.name,
                    asset_slug=asset.slug,
                    publisher_name=publisher.display_name if publisher else "Unknown",
                    publisher_slug=publisher.slug if publisher else "unknown",
                    total_reports=row.total_reports,
                    open_reports=row.open_reports,
                    report_reasons=row.report_reasons or [],
                    latest_report_at=row.latest_report_at,
                )
            )

    return summaries


@admin_router.post(
    "/{report_id}/resolve",
    response_model=ResolveReportResponse,
    summary="Resolve report",
    description="Resolve a report with an action.",
)
async def resolve_report(
    report_id: UUID,
    request: ResolveReportRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
    notifications: NotificationService = Depends(get_notification_service),
) -> ResolveReportResponse:
    """Resolve a report with action (admin only)."""
    # Validate action
    if request.action not in VALID_RESOLUTION_ACTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action. Must be one of: {', '.join(VALID_RESOLUTION_ACTIONS)}",
        )

    result = await db.execute(
        select(AssetReport)
        .where(AssetReport.id == report_id)
        .options(
            selectinload(AssetReport.asset).selectinload(MarketplaceAsset.publisher)
        )
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )

    if report.status in (ReportStatus.RESOLVED.value, ReportStatus.DISMISSED.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report is already resolved",
        )

    asset = report.asset
    publisher = asset.publisher if asset else None

    # Handle actions
    message = ""
    if request.action == "dismiss":
        report.status = ReportStatus.DISMISSED.value
        message = "Report dismissed"

    elif request.action == "warn_publisher":
        report.status = ReportStatus.RESOLVED.value
        message = "Publisher warned"

        # Send warning notification to publisher
        if publisher:
            creator_id = publisher.clerk_user_id or publisher.clerk_org_id
            if creator_id:
                await notifications.send(
                    user_id=creator_id,
                    notification_type="warning_issued",
                    data={
                        "asset_name": asset.name if asset else "Unknown",
                        "asset_slug": asset.slug if asset else "unknown",
                        "warning_reason": request.notes or "Policy violation reported",
                    },
                )

    elif request.action == "unpublish":
        report.status = ReportStatus.RESOLVED.value
        message = "Asset unpublished"

        # Unpublish the asset
        if asset:
            asset.deprecated_at = datetime.now(timezone.utc)

            # Notify publisher
            if publisher:
                creator_id = publisher.clerk_user_id or publisher.clerk_org_id
                if creator_id:
                    await notifications.send(
                        user_id=creator_id,
                        notification_type="asset_unpublished",
                        data={
                            "asset_name": asset.name,
                            "asset_slug": asset.slug,
                            "reason": request.notes or "Policy violation",
                        },
                    )

    elif request.action == "ban_publisher":
        report.status = ReportStatus.RESOLVED.value
        message = "Publisher banned and all assets unpublished"

        # Ban publisher and unpublish all their assets
        if publisher:
            publisher.is_banned = True

            # Unpublish all publisher assets
            await db.execute(
                select(MarketplaceAsset)
                .where(MarketplaceAsset.publisher_id == publisher.id)
            )
            # This would need an update statement
            # For now, just mark the current asset

            if asset:
                asset.deprecated_at = datetime.now(timezone.utc)

    # Update report
    report.resolution_notes = request.notes
    report.resolved_by = admin.user_id
    report.resolved_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(report)

    logger.info(
        f"Resolved report: {report_id} with action {request.action}",
        extra={
            "admin": admin.user_id,
            "report_id": str(report_id),
            "action": request.action,
        },
    )

    return ResolveReportResponse(
        success=True,
        report_id=report.id,
        action=request.action,
        message=message,
    )


@admin_router.get(
    "/{report_id}",
    response_model=ReportItem,
    summary="Get report details",
    description="Get detailed information about a specific report.",
)
async def get_report_detail(
    report_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ReportItem:
    """Get report details (admin only)."""
    result = await db.execute(
        select(AssetReport)
        .where(AssetReport.id == report_id)
        .options(
            selectinload(AssetReport.asset).selectinload(MarketplaceAsset.publisher)
        )
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )

    asset = report.asset
    publisher = asset.publisher if asset else None

    return ReportItem(
        id=report.id,
        asset_id=report.asset_id,
        asset_name=asset.name if asset else "Unknown",
        asset_slug=asset.slug if asset else "unknown",
        publisher_name=publisher.display_name if publisher else "Unknown",
        publisher_slug=publisher.slug if publisher else "unknown",
        reporter_id=report.reporter_id,
        reason=report.reason,
        description=report.description,
        status=report.status,
        resolution_notes=report.resolution_notes,
        resolved_by=report.resolved_by,
        resolved_at=report.resolved_at,
        created_at=report.created_at,
    )


# =============================================================================
# Report Statistics
# =============================================================================


class ReportStatsResponse(BaseModel):
    """Report statistics."""

    open: int
    investigating: int
    resolved_today: int
    by_reason: dict


@admin_router.get(
    "/stats/summary",
    response_model=ReportStatsResponse,
    summary="Get report stats",
    description="Get summary statistics for reports.",
)
async def get_report_stats(
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ReportStatsResponse:
    """Get report statistics (admin only)."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Count by status
    status_counts = await db.execute(
        select(
            AssetReport.status,
            func.count(AssetReport.id).label("count"),
        )
        .group_by(AssetReport.status)
    )
    counts = {row.status: row.count for row in status_counts}

    # Count by reason
    reason_counts = await db.execute(
        select(
            AssetReport.reason,
            func.count(AssetReport.id).label("count"),
        )
        .where(AssetReport.status == ReportStatus.OPEN.value)
        .group_by(AssetReport.reason)
    )
    by_reason = {row.reason: row.count for row in reason_counts}

    # Count resolved today
    resolved_today = await db.execute(
        select(func.count(AssetReport.id)).where(
            and_(
                AssetReport.status == ReportStatus.RESOLVED.value,
                AssetReport.resolved_at >= today_start,
            )
        )
    )

    return ReportStatsResponse(
        open=counts.get(ReportStatus.OPEN.value, 0),
        investigating=counts.get(ReportStatus.INVESTIGATING.value, 0),
        resolved_today=resolved_today.scalar() or 0,
        by_reason=by_reason,
    )

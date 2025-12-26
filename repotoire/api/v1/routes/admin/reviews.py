"""Admin API routes for asset security review management.

These endpoints require admin authentication and provide full
control over the asset review queue and community reports.
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

from repotoire.api.shared.auth import ClerkUser, require_org_admin
from repotoire.api.services.notifications import NotificationService, get_notification_service
from repotoire.db.models.marketplace import (
    AssetReviewStatus,
    AssetSecurityReview,
    AssetReport,
    MarketplaceAsset,
    MarketplaceAssetVersion,
    MarketplacePublisher,
    ReportReason,
    ReportStatus,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/reviews", tags=["admin", "reviews"])


# =============================================================================
# Request/Response Models
# =============================================================================


class ScanFindingResponse(BaseModel):
    """Scan finding from automated security scan."""

    severity: str
    category: str
    message: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    pattern_matched: Optional[str] = None


class ReviewQueueItem(BaseModel):
    """Item in the review queue."""

    id: UUID
    asset_version_id: UUID
    asset_name: str
    asset_slug: str
    publisher_name: str
    publisher_slug: str
    version: str
    status: str
    scan_verdict: Optional[str] = None
    scan_findings_count: int = 0
    scan_findings_critical: int = 0
    scan_findings_high: int = 0
    scanned_at: Optional[datetime] = None
    reviewer_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReviewDetailResponse(BaseModel):
    """Detailed review information."""

    id: UUID
    asset_version_id: UUID
    asset_name: str
    asset_slug: str
    publisher_name: str
    publisher_slug: str
    version: str
    status: str
    scan_verdict: Optional[str] = None
    scan_findings: List[ScanFindingResponse] = []
    scanned_at: Optional[datetime] = None
    reviewer_id: Optional[str] = None
    reviewer_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    changes_requested: Optional[List[str]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReviewQueueResponse(BaseModel):
    """Paginated review queue response."""

    items: List[ReviewQueueItem]
    total: int
    page: int
    limit: int


class ApproveRequest(BaseModel):
    """Request to approve an asset."""

    notes: Optional[str] = Field(None, description="Optional reviewer notes")


class RejectRequest(BaseModel):
    """Request to reject an asset."""

    reason: str = Field(..., min_length=10, description="Rejection reason (required)")


class RequestChangesRequest(BaseModel):
    """Request to request changes from publisher."""

    changes: List[str] = Field(..., min_items=1, description="List of requested changes")


class ReviewActionResponse(BaseModel):
    """Response from a review action."""

    success: bool
    review_id: UUID
    status: str
    message: str


# =============================================================================
# Review Queue Endpoints
# =============================================================================


@router.get(
    "/queue",
    response_model=ReviewQueueResponse,
    summary="Get review queue",
    description="Get assets pending review, filtered by status.",
)
async def get_review_queue(
    status_filter: AssetReviewStatus = Query(
        AssetReviewStatus.PENDING,
        alias="status",
        description="Filter by review status",
    ),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=50, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ReviewQueueResponse:
    """Get assets pending review (admin only)."""
    # Build query
    query = (
        select(AssetSecurityReview)
        .where(AssetSecurityReview.status == status_filter.value)
        .options(
            selectinload(AssetSecurityReview.asset_version).selectinload(
                MarketplaceAssetVersion.asset
            ).selectinload(MarketplaceAsset.publisher)
        )
        .order_by(desc(AssetSecurityReview.created_at))
    )

    # Count total
    count_query = select(func.count()).select_from(
        select(AssetSecurityReview)
        .where(AssetSecurityReview.status == status_filter.value)
        .subquery()
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * limit
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    reviews = list(result.scalars().all())

    # Build response items
    items = []
    for review in reviews:
        version = review.asset_version
        asset = version.asset if version else None
        publisher = asset.publisher if asset else None

        # Count findings by severity
        findings = review.scan_findings or []
        critical_count = sum(1 for f in findings if f.get("severity") == "critical")
        high_count = sum(1 for f in findings if f.get("severity") == "high")

        items.append(
            ReviewQueueItem(
                id=review.id,
                asset_version_id=review.asset_version_id,
                asset_name=asset.name if asset else "Unknown",
                asset_slug=asset.slug if asset else "unknown",
                publisher_name=publisher.display_name if publisher else "Unknown",
                publisher_slug=publisher.slug if publisher else "unknown",
                version=version.version if version else "0.0.0",
                status=review.status,
                scan_verdict=review.scan_verdict,
                scan_findings_count=len(findings),
                scan_findings_critical=critical_count,
                scan_findings_high=high_count,
                scanned_at=review.scanned_at,
                reviewer_id=review.reviewer_id,
                created_at=review.created_at,
                updated_at=review.updated_at,
            )
        )

    return ReviewQueueResponse(
        items=items,
        total=total,
        page=page,
        limit=limit,
    )


@router.get(
    "/{review_id}",
    response_model=ReviewDetailResponse,
    summary="Get review details",
    description="Get detailed information about a specific review.",
)
async def get_review_detail(
    review_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ReviewDetailResponse:
    """Get detailed review information."""
    result = await db.execute(
        select(AssetSecurityReview)
        .where(AssetSecurityReview.id == review_id)
        .options(
            selectinload(AssetSecurityReview.asset_version).selectinload(
                MarketplaceAssetVersion.asset
            ).selectinload(MarketplaceAsset.publisher)
        )
    )
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    version = review.asset_version
    asset = version.asset if version else None
    publisher = asset.publisher if asset else None

    # Parse findings
    findings = [
        ScanFindingResponse(**f)
        for f in (review.scan_findings or [])
    ]

    return ReviewDetailResponse(
        id=review.id,
        asset_version_id=review.asset_version_id,
        asset_name=asset.name if asset else "Unknown",
        asset_slug=asset.slug if asset else "unknown",
        publisher_name=publisher.display_name if publisher else "Unknown",
        publisher_slug=publisher.slug if publisher else "unknown",
        version=version.version if version else "0.0.0",
        status=review.status,
        scan_verdict=review.scan_verdict,
        scan_findings=findings,
        scanned_at=review.scanned_at,
        reviewer_id=review.reviewer_id,
        reviewer_notes=review.reviewer_notes,
        reviewed_at=review.reviewed_at,
        changes_requested=review.changes_requested,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@router.post(
    "/{review_id}/approve",
    response_model=ReviewActionResponse,
    summary="Approve asset",
    description="Approve an asset for publication.",
)
async def approve_asset(
    review_id: UUID,
    request: ApproveRequest = Body(default=ApproveRequest()),
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
    notifications: NotificationService = Depends(get_notification_service),
) -> ReviewActionResponse:
    """Approve an asset for publication."""
    result = await db.execute(
        select(AssetSecurityReview)
        .where(AssetSecurityReview.id == review_id)
        .options(
            selectinload(AssetSecurityReview.asset_version).selectinload(
                MarketplaceAssetVersion.asset
            ).selectinload(MarketplaceAsset.publisher)
        )
    )
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    # Check current status
    if review.status == AssetReviewStatus.APPROVED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review is already approved",
        )

    # Update review
    review.status = AssetReviewStatus.APPROVED.value
    review.reviewer_id = admin.user_id
    review.reviewer_notes = request.notes
    review.reviewed_at = datetime.now(timezone.utc)

    # Publish the version
    version = review.asset_version
    if version and not version.published_at:
        version.published_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(review)

    # Send notification to publisher
    asset = version.asset if version else None
    publisher = asset.publisher if asset else None
    if publisher:
        creator_id = publisher.clerk_user_id or publisher.clerk_org_id
        if creator_id:
            await notifications.send(
                user_id=creator_id,
                notification_type="asset_approved",
                data={
                    "asset_name": asset.name if asset else "Unknown",
                    "asset_slug": asset.slug if asset else "unknown",
                    "version": version.version if version else "0.0.0",
                    "reviewer_notes": request.notes,
                },
            )

    logger.info(
        f"Approved asset review: {review_id}",
        extra={"admin": admin.user_id, "review_id": str(review_id)},
    )

    return ReviewActionResponse(
        success=True,
        review_id=review.id,
        status=review.status,
        message="Asset approved and published successfully",
    )


@router.post(
    "/{review_id}/reject",
    response_model=ReviewActionResponse,
    summary="Reject asset",
    description="Reject an asset with reason.",
)
async def reject_asset(
    review_id: UUID,
    request: RejectRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
    notifications: NotificationService = Depends(get_notification_service),
) -> ReviewActionResponse:
    """Reject an asset with reason."""
    result = await db.execute(
        select(AssetSecurityReview)
        .where(AssetSecurityReview.id == review_id)
        .options(
            selectinload(AssetSecurityReview.asset_version).selectinload(
                MarketplaceAssetVersion.asset
            ).selectinload(MarketplaceAsset.publisher)
        )
    )
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    # Check current status
    if review.status == AssetReviewStatus.REJECTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review is already rejected",
        )

    # Update review
    review.status = AssetReviewStatus.REJECTED.value
    review.reviewer_id = admin.user_id
    review.reviewer_notes = request.reason
    review.reviewed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(review)

    # Send notification to publisher
    version = review.asset_version
    asset = version.asset if version else None
    publisher = asset.publisher if asset else None
    if publisher:
        creator_id = publisher.clerk_user_id or publisher.clerk_org_id
        if creator_id:
            await notifications.send(
                user_id=creator_id,
                notification_type="asset_rejected",
                data={
                    "asset_name": asset.name if asset else "Unknown",
                    "asset_slug": asset.slug if asset else "unknown",
                    "version": version.version if version else "0.0.0",
                    "rejection_reason": request.reason,
                },
            )

    logger.info(
        f"Rejected asset review: {review_id}",
        extra={"admin": admin.user_id, "review_id": str(review_id)},
    )

    return ReviewActionResponse(
        success=True,
        review_id=review.id,
        status=review.status,
        message="Asset rejected",
    )


@router.post(
    "/{review_id}/request-changes",
    response_model=ReviewActionResponse,
    summary="Request changes",
    description="Request changes before approval.",
)
async def request_changes(
    review_id: UUID,
    request: RequestChangesRequest,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
    notifications: NotificationService = Depends(get_notification_service),
) -> ReviewActionResponse:
    """Request changes before approval."""
    result = await db.execute(
        select(AssetSecurityReview)
        .where(AssetSecurityReview.id == review_id)
        .options(
            selectinload(AssetSecurityReview.asset_version).selectinload(
                MarketplaceAssetVersion.asset
            ).selectinload(MarketplaceAsset.publisher)
        )
    )
    review = result.scalar_one_or_none()

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    # Update review
    review.status = AssetReviewStatus.REQUIRES_CHANGES.value
    review.reviewer_id = admin.user_id
    review.changes_requested = request.changes
    review.reviewed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(review)

    # Send notification to publisher
    version = review.asset_version
    asset = version.asset if version else None
    publisher = asset.publisher if asset else None
    if publisher:
        creator_id = publisher.clerk_user_id or publisher.clerk_org_id
        if creator_id:
            await notifications.send(
                user_id=creator_id,
                notification_type="changes_requested",
                data={
                    "asset_name": asset.name if asset else "Unknown",
                    "asset_slug": asset.slug if asset else "unknown",
                    "version": version.version if version else "0.0.0",
                    "changes": request.changes,
                },
            )

    logger.info(
        f"Requested changes for asset review: {review_id}",
        extra={"admin": admin.user_id, "review_id": str(review_id)},
    )

    return ReviewActionResponse(
        success=True,
        review_id=review.id,
        status=review.status,
        message=f"Requested {len(request.changes)} change(s) from publisher",
    )


@router.post(
    "/{review_id}/claim",
    response_model=ReviewActionResponse,
    summary="Claim review",
    description="Claim a review to indicate you're reviewing it.",
)
async def claim_review(
    review_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ReviewActionResponse:
    """Claim a review for manual review."""
    review = await db.get(AssetSecurityReview, review_id)

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    # Check if already claimed by someone else
    if review.reviewer_id and review.reviewer_id != admin.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review is already claimed by another reviewer",
        )

    # Claim the review
    review.status = AssetReviewStatus.IN_REVIEW.value
    review.reviewer_id = admin.user_id

    await db.commit()
    await db.refresh(review)

    logger.info(
        f"Claimed asset review: {review_id}",
        extra={"admin": admin.user_id, "review_id": str(review_id)},
    )

    return ReviewActionResponse(
        success=True,
        review_id=review.id,
        status=review.status,
        message="Review claimed successfully",
    )


@router.post(
    "/{review_id}/unclaim",
    response_model=ReviewActionResponse,
    summary="Unclaim review",
    description="Release a claimed review back to the queue.",
)
async def unclaim_review(
    review_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> ReviewActionResponse:
    """Release a claimed review back to the queue."""
    review = await db.get(AssetSecurityReview, review_id)

    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    # Only the claimer can unclaim (or any admin if needed)
    if review.reviewer_id != admin.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only unclaim reviews you have claimed",
        )

    # Unclaim the review
    review.status = AssetReviewStatus.PENDING.value
    review.reviewer_id = None

    await db.commit()
    await db.refresh(review)

    logger.info(
        f"Unclaimed asset review: {review_id}",
        extra={"admin": admin.user_id, "review_id": str(review_id)},
    )

    return ReviewActionResponse(
        success=True,
        review_id=review.id,
        status=review.status,
        message="Review released back to queue",
    )


# =============================================================================
# Queue Statistics
# =============================================================================


class QueueStatsResponse(BaseModel):
    """Review queue statistics."""

    pending: int
    in_review: int
    requires_changes: int
    approved_today: int
    rejected_today: int


@router.get(
    "/stats/summary",
    response_model=QueueStatsResponse,
    summary="Get queue stats",
    description="Get summary statistics for the review queue.",
)
async def get_queue_stats(
    db: AsyncSession = Depends(get_db),
    admin: ClerkUser = Depends(require_org_admin),
) -> QueueStatsResponse:
    """Get review queue statistics."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Count by status
    status_counts = await db.execute(
        select(
            AssetSecurityReview.status,
            func.count(AssetSecurityReview.id).label("count"),
        )
        .group_by(AssetSecurityReview.status)
    )
    counts = {row.status: row.count for row in status_counts}

    # Count approved/rejected today
    approved_today = await db.execute(
        select(func.count(AssetSecurityReview.id)).where(
            and_(
                AssetSecurityReview.status == AssetReviewStatus.APPROVED.value,
                AssetSecurityReview.reviewed_at >= today_start,
            )
        )
    )
    rejected_today = await db.execute(
        select(func.count(AssetSecurityReview.id)).where(
            and_(
                AssetSecurityReview.status == AssetReviewStatus.REJECTED.value,
                AssetSecurityReview.reviewed_at >= today_start,
            )
        )
    )

    return QueueStatsResponse(
        pending=counts.get(AssetReviewStatus.PENDING.value, 0),
        in_review=counts.get(AssetReviewStatus.IN_REVIEW.value, 0),
        requires_changes=counts.get(AssetReviewStatus.REQUIRES_CHANGES.value, 0),
        approved_today=approved_today.scalar() or 0,
        rejected_today=rejected_today.scalar() or 0,
    )

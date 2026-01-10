"""API routes for notifications (email preferences and in-app notifications).

This module provides endpoints for:
- Managing user email notification preferences
- Fetching, marking as read, and deleting in-app notifications
"""

from datetime import datetime, timezone
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.db.models import EmailPreferences, InAppNotification, NotificationType, User
from repotoire.db.session import get_db

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# =============================================================================
# Request/Response Models
# =============================================================================


class EmailPreferencesRequest(BaseModel):
    """Request model for updating email preferences."""

    analysis_complete: bool = Field(
        default=True,
        description="Notify when analysis completes successfully",
    )
    analysis_failed: bool = Field(
        default=True,
        description="Notify when analysis fails",
    )
    health_regression: bool = Field(
        default=True,
        description="Notify when health score drops significantly",
    )
    weekly_digest: bool = Field(
        default=False,
        description="Send weekly summary email",
    )
    team_notifications: bool = Field(
        default=True,
        description="Notify about team changes (invites, role changes)",
    )
    billing_notifications: bool = Field(
        default=True,
        description="Notify about billing events",
    )
    in_app_notifications: bool = Field(
        default=True,
        description="Enable in-app notifications",
    )
    regression_threshold: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Minimum score drop to trigger regression alert",
    )


class EmailPreferencesResponse(BaseModel):
    """Response model for email preferences."""

    analysis_complete: bool
    analysis_failed: bool
    health_regression: bool
    weekly_digest: bool
    team_notifications: bool
    billing_notifications: bool
    in_app_notifications: bool = True
    regression_threshold: int

    model_config = {"from_attributes": True}


class NotificationResponse(BaseModel):
    """Response model for a single notification."""

    id: UUID
    type: str
    title: str
    message: str
    read: bool
    read_at: Optional[datetime] = None
    action_url: Optional[str] = None
    metadata: Optional[dict[str, Any]] = Field(None, validation_alias="extra_data")
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class NotificationsListResponse(BaseModel):
    """Response model for list of notifications."""

    notifications: List[NotificationResponse]
    unread_count: int
    total: int


class MarkReadRequest(BaseModel):
    """Request model for marking notifications as read."""

    notification_ids: List[UUID] = Field(
        ...,
        description="List of notification IDs to mark as read",
    )


class MarkReadResponse(BaseModel):
    """Response model for mark as read operation."""

    marked_count: int
    unread_count: int


class DeleteNotificationsRequest(BaseModel):
    """Request model for deleting notifications."""

    notification_ids: List[UUID] = Field(
        ...,
        description="List of notification IDs to delete",
    )


class DeleteNotificationsResponse(BaseModel):
    """Response model for delete operation."""

    deleted_count: int


# =============================================================================
# Helper Functions
# =============================================================================


async def get_user_by_clerk_id(
    session: AsyncSession,
    clerk_user_id: str,
) -> User | None:
    """Get user by Clerk user ID.

    Args:
        session: Database session.
        clerk_user_id: Clerk user identifier.

    Returns:
        User if found, None otherwise.
    """
    result = await session.execute(
        select(User).where(User.clerk_user_id == clerk_user_id)
    )
    return result.scalar_one_or_none()


# =============================================================================
# Email Preferences Routes
# =============================================================================


@router.get("/preferences", response_model=EmailPreferencesResponse)
async def get_email_preferences(
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> EmailPreferencesResponse:
    """Get current user's email notification preferences.

    Returns the user's notification settings. If no preferences exist,
    returns default values.
    """
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get preferences with eager loading
    result = await session.execute(
        select(EmailPreferences).where(EmailPreferences.user_id == user.id)
    )
    prefs = result.scalar_one_or_none()

    if not prefs:
        # Return defaults
        return EmailPreferencesResponse(
            analysis_complete=True,
            analysis_failed=True,
            health_regression=True,
            weekly_digest=False,
            team_notifications=True,
            billing_notifications=True,
            in_app_notifications=True,
            regression_threshold=10,
        )

    return EmailPreferencesResponse.model_validate(prefs)


@router.put("/preferences", response_model=EmailPreferencesResponse)
async def update_email_preferences(
    preferences: EmailPreferencesRequest,
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> EmailPreferencesResponse:
    """Update user's email notification preferences.

    Creates preferences if they don't exist, otherwise updates existing.
    """
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get or create preferences
    result = await session.execute(
        select(EmailPreferences).where(EmailPreferences.user_id == user.id)
    )
    prefs = result.scalar_one_or_none()

    if not prefs:
        prefs = EmailPreferences(user_id=user.id)
        session.add(prefs)

    # Update all fields
    for field, value in preferences.model_dump().items():
        setattr(prefs, field, value)

    await session.commit()
    await session.refresh(prefs)

    return EmailPreferencesResponse.model_validate(prefs)


@router.post("/preferences/reset", response_model=EmailPreferencesResponse)
async def reset_email_preferences(
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> EmailPreferencesResponse:
    """Reset email preferences to defaults.

    Deletes existing preferences and returns default values.
    """
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Delete existing preferences
    result = await session.execute(
        select(EmailPreferences).where(EmailPreferences.user_id == user.id)
    )
    prefs = result.scalar_one_or_none()

    if prefs:
        await session.delete(prefs)
        await session.commit()

    # Return defaults
    return EmailPreferencesResponse(
        analysis_complete=True,
        analysis_failed=True,
        health_regression=True,
        weekly_digest=False,
        team_notifications=True,
        billing_notifications=True,
        in_app_notifications=True,
        regression_threshold=10,
    )


# =============================================================================
# In-App Notifications Routes
# =============================================================================


@router.get("", response_model=NotificationsListResponse)
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=100, description="Max notifications to return"),
    offset: int = Query(default=0, ge=0, description="Number of notifications to skip"),
    unread_only: bool = Query(default=False, description="Only return unread notifications"),
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> NotificationsListResponse:
    """List notifications for the current user.

    Returns paginated list of notifications, ordered by creation date (newest first).
    Includes unread count for badge display.
    """
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Build query
    query = select(InAppNotification).where(InAppNotification.user_id == user.id)

    if unread_only:
        query = query.where(InAppNotification.read == False)  # noqa: E712

    query = query.order_by(InAppNotification.created_at.desc()).offset(offset).limit(limit)

    result = await session.execute(query)
    notifications = result.scalars().all()

    # Get unread count
    unread_result = await session.execute(
        select(func.count(InAppNotification.id)).where(
            InAppNotification.user_id == user.id,
            InAppNotification.read == False,  # noqa: E712
        )
    )
    unread_count = unread_result.scalar_one()

    # Get total count
    total_result = await session.execute(
        select(func.count(InAppNotification.id)).where(
            InAppNotification.user_id == user.id,
        )
    )
    total = total_result.scalar_one()

    return NotificationsListResponse(
        notifications=[NotificationResponse.model_validate(n) for n in notifications],
        unread_count=unread_count,
        total=total,
    )


@router.get("/unread-count")
async def get_unread_count(
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Get the count of unread notifications.

    Lightweight endpoint for updating notification badge.
    """
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await session.execute(
        select(func.count(InAppNotification.id)).where(
            InAppNotification.user_id == user.id,
            InAppNotification.read == False,  # noqa: E712
        )
    )
    count = result.scalar_one()

    return {"unread_count": count}


@router.post("/mark-read", response_model=MarkReadResponse)
async def mark_notifications_read(
    request: MarkReadRequest,
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MarkReadResponse:
    """Mark specific notifications as read.

    Only marks notifications belonging to the current user.
    """
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)

    # Update notifications
    result = await session.execute(
        update(InAppNotification)
        .where(
            InAppNotification.id.in_(request.notification_ids),
            InAppNotification.user_id == user.id,
            InAppNotification.read == False,  # noqa: E712
        )
        .values(read=True, read_at=now)
    )
    await session.commit()

    # Get new unread count
    unread_result = await session.execute(
        select(func.count(InAppNotification.id)).where(
            InAppNotification.user_id == user.id,
            InAppNotification.read == False,  # noqa: E712
        )
    )
    unread_count = unread_result.scalar_one()

    return MarkReadResponse(
        marked_count=result.rowcount,
        unread_count=unread_count,
    )


@router.post("/mark-all-read", response_model=MarkReadResponse)
async def mark_all_notifications_read(
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MarkReadResponse:
    """Mark all notifications as read for the current user."""
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    now = datetime.now(timezone.utc)

    # Update all unread notifications
    result = await session.execute(
        update(InAppNotification)
        .where(
            InAppNotification.user_id == user.id,
            InAppNotification.read == False,  # noqa: E712
        )
        .values(read=True, read_at=now)
    )
    await session.commit()

    return MarkReadResponse(
        marked_count=result.rowcount,
        unread_count=0,
    )


@router.delete("", response_model=DeleteNotificationsResponse)
async def delete_notifications(
    request: DeleteNotificationsRequest,
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DeleteNotificationsResponse:
    """Delete specific notifications.

    Only deletes notifications belonging to the current user.
    """
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await session.execute(
        delete(InAppNotification).where(
            InAppNotification.id.in_(request.notification_ids),
            InAppNotification.user_id == user.id,
        )
    )
    await session.commit()

    return DeleteNotificationsResponse(deleted_count=result.rowcount)


@router.delete("/all", response_model=DeleteNotificationsResponse)
async def delete_all_notifications(
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DeleteNotificationsResponse:
    """Delete all notifications for the current user."""
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await session.execute(
        delete(InAppNotification).where(InAppNotification.user_id == user.id)
    )
    await session.commit()

    return DeleteNotificationsResponse(deleted_count=result.rowcount)


@router.get("/{notification_id}", response_model=NotificationResponse)
async def get_notification(
    notification_id: UUID,
    current_user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> NotificationResponse:
    """Get a single notification by ID."""
    user = await get_user_by_clerk_id(session, current_user.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await session.execute(
        select(InAppNotification).where(
            InAppNotification.id == notification_id,
            InAppNotification.user_id == user.id,
        )
    )
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    return NotificationResponse.model_validate(notification)

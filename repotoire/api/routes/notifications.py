"""API routes for email notification preferences.

This module provides endpoints for managing user email notification
preferences, including getting and updating notification settings.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.auth import ClerkUser, get_current_user
from repotoire.db.models import EmailPreferences, User
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
    regression_threshold: int

    model_config = {"from_attributes": True}


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
# API Routes
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
        regression_threshold=10,
    )

"""User preferences API routes.

This module provides API endpoints for managing user preferences,
controlling appearance, notification, and auto-fix settings.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.api.shared.helpers import get_or_create_db_user
from repotoire.db.models import User, UserPreferences
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/account/preferences", tags=["account"])


# ============================================================================
# Request/Response Models
# ============================================================================


class UserPreferencesResponse(BaseModel):
    """Response with user preferences."""

    # Appearance
    theme: str = Field(
        default="system",
        description="Theme preference ('light', 'dark', 'system')",
    )

    # Notifications
    new_fix_alerts: bool = Field(
        default=True,
        description="Enable alerts for new fixes",
    )
    critical_security_alerts: bool = Field(
        default=True,
        description="Enable alerts for critical security fixes",
    )
    weekly_summary: bool = Field(
        default=False,
        description="Enable weekly summary emails",
    )

    # Auto-fix settings
    auto_approve_high_confidence: bool = Field(
        default=False,
        description="Auto-approve high confidence fixes",
    )
    generate_tests: bool = Field(
        default=True,
        description="Generate tests for applied fixes",
    )
    create_git_branches: bool = Field(
        default=True,
        description="Create separate branches for each fix",
    )

    model_config = {"from_attributes": True}


class UserPreferencesUpdate(BaseModel):
    """Request to update user preferences."""

    # Appearance
    theme: str | None = Field(
        default=None,
        description="Theme preference ('light', 'dark', 'system')",
    )

    # Notifications
    new_fix_alerts: bool | None = Field(
        default=None,
        description="Enable alerts for new fixes",
    )
    critical_security_alerts: bool | None = Field(
        default=None,
        description="Enable alerts for critical security fixes",
    )
    weekly_summary: bool | None = Field(
        default=None,
        description="Enable weekly summary emails",
    )

    # Auto-fix settings
    auto_approve_high_confidence: bool | None = Field(
        default=None,
        description="Auto-approve high confidence fixes",
    )
    generate_tests: bool | None = Field(
        default=None,
        description="Generate tests for applied fixes",
    )
    create_git_branches: bool | None = Field(
        default=None,
        description="Create separate branches for each fix",
    )


# ============================================================================
# Helper Functions
# ============================================================================


async def get_or_create_preferences(db: AsyncSession, user: User) -> UserPreferences:
    """Get or create user preferences.

    Args:
        db: Database session
        user: User model instance

    Returns:
        UserPreferences model instance with defaults if not found
    """
    result = await db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    )
    preferences = result.scalar_one_or_none()

    if not preferences:
        # Create default preferences
        preferences = UserPreferences(
            user_id=user.id,
            theme="system",
            new_fix_alerts=True,
            critical_security_alerts=True,
            weekly_summary=False,
            auto_approve_high_confidence=False,
            generate_tests=True,
            create_git_branches=True,
        )
        db.add(preferences)
        await db.flush()

    return preferences


# ============================================================================
# Routes
# ============================================================================


@router.get("", response_model=UserPreferencesResponse)
async def get_preferences(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserPreferencesResponse:
    """Get current user preferences.

    Returns the user's preferences for appearance, notifications,
    and auto-fix settings. Default settings are returned if not set.
    """
    db_user = await get_or_create_db_user(db, user)
    preferences = await get_or_create_preferences(db, db_user)
    await db.commit()

    return UserPreferencesResponse(
        theme=preferences.theme,
        new_fix_alerts=preferences.new_fix_alerts,
        critical_security_alerts=preferences.critical_security_alerts,
        weekly_summary=preferences.weekly_summary,
        auto_approve_high_confidence=preferences.auto_approve_high_confidence,
        generate_tests=preferences.generate_tests,
        create_git_branches=preferences.create_git_branches,
    )


@router.put("", response_model=UserPreferencesResponse)
async def update_preferences(
    update: UserPreferencesUpdate,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserPreferencesResponse:
    """Update user preferences.

    Updates the user's preferences. Only provided fields will be updated.
    """
    db_user = await get_or_create_db_user(db, user)
    preferences = await get_or_create_preferences(db, db_user)

    # Update only provided fields
    if update.theme is not None:
        # Validate theme value
        if update.theme not in ("light", "dark", "system"):
            update.theme = "system"
        preferences.theme = update.theme
    if update.new_fix_alerts is not None:
        preferences.new_fix_alerts = update.new_fix_alerts
    if update.critical_security_alerts is not None:
        preferences.critical_security_alerts = update.critical_security_alerts
    if update.weekly_summary is not None:
        preferences.weekly_summary = update.weekly_summary
    if update.auto_approve_high_confidence is not None:
        preferences.auto_approve_high_confidence = update.auto_approve_high_confidence
    if update.generate_tests is not None:
        preferences.generate_tests = update.generate_tests
    if update.create_git_branches is not None:
        preferences.create_git_branches = update.create_git_branches

    await db.commit()

    logger.info(
        f"Updated preferences for user {user.user_id}",
        extra={
            "user_id": user.user_id,
            "preferences": {
                "theme": preferences.theme,
                "new_fix_alerts": preferences.new_fix_alerts,
                "critical_security_alerts": preferences.critical_security_alerts,
                "weekly_summary": preferences.weekly_summary,
                "auto_approve_high_confidence": preferences.auto_approve_high_confidence,
                "generate_tests": preferences.generate_tests,
                "create_git_branches": preferences.create_git_branches,
            },
        },
    )

    return UserPreferencesResponse(
        theme=preferences.theme,
        new_fix_alerts=preferences.new_fix_alerts,
        critical_security_alerts=preferences.critical_security_alerts,
        weekly_summary=preferences.weekly_summary,
        auto_approve_high_confidence=preferences.auto_approve_high_confidence,
        generate_tests=preferences.generate_tests,
        create_git_branches=preferences.create_git_branches,
    )

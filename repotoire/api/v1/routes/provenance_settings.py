"""Provenance settings API routes.

This module provides API endpoints for managing user provenance display settings,
controlling how git attribution information appears throughout the dashboard.
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.api.shared.helpers import get_or_create_db_user
from repotoire.db.models import ProvenanceSettings, User
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/account/provenance-settings", tags=["account"])


# ============================================================================
# Request/Response Models
# ============================================================================


class ProvenanceSettingsResponse(BaseModel):
    """Response with provenance display settings."""

    show_author_names: bool = Field(
        default=False,
        description="Display real author names in provenance cards",
    )
    show_author_avatars: bool = Field(
        default=False,
        description="Display author avatars from Gravatar",
    )
    show_confidence_badges: bool = Field(
        default=True,
        description="Display confidence level indicators",
    )
    auto_query_provenance: bool = Field(
        default=False,
        description="Automatically load provenance data on page load",
    )

    model_config = {"from_attributes": True}


class ProvenanceSettingsUpdate(BaseModel):
    """Request to update provenance display settings."""

    show_author_names: bool | None = Field(
        default=None,
        description="Display real author names in provenance cards",
    )
    show_author_avatars: bool | None = Field(
        default=None,
        description="Display author avatars from Gravatar",
    )
    show_confidence_badges: bool | None = Field(
        default=None,
        description="Display confidence level indicators",
    )
    auto_query_provenance: bool | None = Field(
        default=None,
        description="Automatically load provenance data on page load",
    )


# ============================================================================
# Helper Functions
# ============================================================================


async def get_or_create_settings(db: AsyncSession, user: User) -> ProvenanceSettings:
    """Get or create provenance settings for a user.

    Args:
        db: Database session
        user: User model instance

    Returns:
        ProvenanceSettings model instance with defaults if not found
    """
    result = await db.execute(
        select(ProvenanceSettings).where(ProvenanceSettings.user_id == user.id)
    )
    settings = result.scalar_one_or_none()

    if not settings:
        # Create default settings (privacy-first)
        settings = ProvenanceSettings(
            user_id=user.id,
            show_author_names=False,
            show_author_avatars=False,
            show_confidence_badges=True,
            auto_query_provenance=False,
        )
        db.add(settings)
        await db.flush()

    return settings


# ============================================================================
# Routes
# ============================================================================


@router.get("", response_model=ProvenanceSettingsResponse)
async def get_provenance_settings(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProvenanceSettingsResponse:
    """Get current provenance display settings.

    Returns the user's preferences for how git attribution information
    is displayed throughout the dashboard. Default settings are
    privacy-first (author information hidden).
    """
    db_user = await get_or_create_db_user(db, user)
    settings = await get_or_create_settings(db, db_user)
    await db.commit()

    return ProvenanceSettingsResponse(
        show_author_names=settings.show_author_names,
        show_author_avatars=settings.show_author_avatars,
        show_confidence_badges=settings.show_confidence_badges,
        auto_query_provenance=settings.auto_query_provenance,
    )


@router.put("", response_model=ProvenanceSettingsResponse)
async def update_provenance_settings(
    update: ProvenanceSettingsUpdate,
    request: Request,
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProvenanceSettingsResponse:
    """Update provenance display settings.

    Updates the user's preferences for how git attribution information
    is displayed. Only provided fields will be updated.

    Privacy notes:
    - show_author_names: When enabled, shows real developer names
    - show_author_avatars: When enabled, loads Gravatar images
    - auto_query_provenance: When enabled, may slow page loads
    """
    db_user = await get_or_create_db_user(db, user)
    settings = await get_or_create_settings(db, db_user)

    # Update only provided fields
    if update.show_author_names is not None:
        settings.show_author_names = update.show_author_names
    if update.show_author_avatars is not None:
        settings.show_author_avatars = update.show_author_avatars
    if update.show_confidence_badges is not None:
        settings.show_confidence_badges = update.show_confidence_badges
    if update.auto_query_provenance is not None:
        settings.auto_query_provenance = update.auto_query_provenance

    await db.commit()

    logger.info(
        f"Updated provenance settings for user {user.user_id}",
        extra={
            "user_id": user.user_id,
            "settings": {
                "show_author_names": settings.show_author_names,
                "show_author_avatars": settings.show_author_avatars,
                "show_confidence_badges": settings.show_confidence_badges,
                "auto_query_provenance": settings.auto_query_provenance,
            },
        },
    )

    return ProvenanceSettingsResponse(
        show_author_names=settings.show_author_names,
        show_author_avatars=settings.show_author_avatars,
        show_confidence_badges=settings.show_confidence_badges,
        auto_query_provenance=settings.auto_query_provenance,
    )

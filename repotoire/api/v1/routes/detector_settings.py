"""Detector settings API routes.

This module provides API endpoints for managing organization-level
detector threshold configuration, allowing teams to customize sensitivity
levels or define custom thresholds.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.shared.auth import ClerkUser, get_current_user
from repotoire.db.models import (
    DetectorPreset,
    DetectorSettings,
    MemberRole,
    Organization,
    OrganizationMembership,
    PRESET_THRESHOLDS,
    User,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/orgs", tags=["detector-settings"])


# =============================================================================
# Request/Response Models
# =============================================================================


class ThresholdConfig(BaseModel):
    """Individual threshold configuration values."""

    # God Class thresholds
    god_class_high_method_count: int = Field(
        default=20, description="Method count threshold for high severity"
    )
    god_class_medium_method_count: int = Field(
        default=15, description="Method count threshold for medium severity"
    )
    god_class_high_complexity: int = Field(
        default=100, description="Complexity threshold for high severity"
    )
    god_class_medium_complexity: int = Field(
        default=50, description="Complexity threshold for medium severity"
    )
    god_class_high_loc: int = Field(
        default=500, description="Lines of code threshold for high severity"
    )
    god_class_medium_loc: int = Field(
        default=300, description="Lines of code threshold for medium severity"
    )
    god_class_high_lcom: float = Field(
        default=0.8, description="LCOM threshold for high severity (0-1)"
    )
    god_class_medium_lcom: float = Field(
        default=0.6, description="LCOM threshold for medium severity (0-1)"
    )

    # Feature Envy thresholds
    feature_envy_threshold_ratio: float = Field(
        default=3.0, description="External/internal usage ratio threshold"
    )
    feature_envy_min_external_uses: int = Field(
        default=15, description="Minimum external uses to flag"
    )

    # Radon thresholds
    radon_complexity_threshold: int = Field(
        default=10, description="Cyclomatic complexity threshold"
    )
    radon_maintainability_threshold: int = Field(
        default=65, description="Maintainability index threshold"
    )

    # Global settings
    max_findings_per_detector: int = Field(
        default=100, description="Maximum findings per detector"
    )
    confidence_threshold: float = Field(
        default=0.7, description="Minimum confidence threshold (0-1)"
    )


class DetectorSettingsResponse(BaseModel):
    """Response with detector settings."""

    preset: str = Field(
        description="Current preset: strict, balanced, permissive, custom"
    )
    thresholds: Dict[str, Any] = Field(
        description="Current threshold configuration"
    )
    enabled_detectors: Optional[List[str]] = Field(
        default=None, description="List of enabled detectors (null = all)"
    )
    disabled_detectors: List[str] = Field(
        default_factory=list, description="List of disabled detectors"
    )

    model_config = {"from_attributes": True}


class DetectorSettingsUpdate(BaseModel):
    """Request to update detector settings."""

    thresholds: Optional[Dict[str, Any]] = Field(
        default=None, description="Threshold values to update (partial update)"
    )
    enabled_detectors: Optional[List[str]] = Field(
        default=None, description="List of enabled detectors (null = all)"
    )
    disabled_detectors: Optional[List[str]] = Field(
        default=None, description="List of disabled detectors"
    )


class PresetListResponse(BaseModel):
    """Response listing available presets."""

    presets: List[Dict[str, Any]] = Field(
        description="Available preset configurations"
    )


# =============================================================================
# Helper Functions
# =============================================================================


async def get_org_by_slug(session: AsyncSession, slug: str) -> Organization | None:
    """Get organization by slug."""
    result = await session.execute(
        select(Organization).where(Organization.slug == slug)
    )
    return result.scalar_one_or_none()


async def get_user_membership(
    session: AsyncSession,
    user: ClerkUser,
    org: Organization,
) -> OrganizationMembership | None:
    """Get user's membership in an organization."""
    db_user = await session.execute(
        select(User).where(User.clerk_user_id == user.user_id)
    )
    user_record = db_user.scalar_one_or_none()
    if not user_record:
        return None

    result = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_record.id,
            OrganizationMembership.organization_id == org.id,
        )
    )
    return result.scalar_one_or_none()


async def require_admin_or_owner(
    session: AsyncSession,
    user: ClerkUser,
    org: Organization,
) -> None:
    """Verify user is admin or owner of the organization."""
    membership = await get_user_membership(session, user, org)
    if not membership or membership.role not in [MemberRole.OWNER, MemberRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner role required to modify detector settings",
        )


async def get_or_create_settings(
    session: AsyncSession, org: Organization
) -> DetectorSettings:
    """Get or create detector settings for an organization."""
    result = await session.execute(
        select(DetectorSettings).where(
            DetectorSettings.organization_id == org.id
        )
    )
    settings = result.scalar_one_or_none()

    if not settings:
        # Create default settings with balanced preset
        settings = DetectorSettings(
            organization_id=org.id,
            preset=DetectorPreset.BALANCED.value,
            thresholds=PRESET_THRESHOLDS[DetectorPreset.BALANCED.value].copy(),
            disabled_detectors=[],
        )
        session.add(settings)
        await session.flush()

    return settings


# =============================================================================
# Routes
# =============================================================================


@router.get(
    "/{slug}/settings/detectors",
    response_model=DetectorSettingsResponse,
    summary="Get detector settings",
    description="Get the organization's detector threshold configuration.",
)
async def get_detector_settings(
    slug: str,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DetectorSettingsResponse:
    """Get current detector settings for an organization.

    Returns the organization's detector configuration including the
    current preset, all threshold values, and detector enable/disable lists.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    membership = await get_user_membership(session, user, org)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization",
        )

    settings = await get_or_create_settings(session, org)
    await session.commit()

    return DetectorSettingsResponse(
        preset=settings.preset,
        thresholds=settings.thresholds,
        enabled_detectors=settings.enabled_detectors,
        disabled_detectors=settings.disabled_detectors or [],
    )


@router.put(
    "/{slug}/settings/detectors",
    response_model=DetectorSettingsResponse,
    summary="Update detector settings",
    description="Update the organization's detector threshold configuration.",
)
async def update_detector_settings(
    slug: str,
    update: DetectorSettingsUpdate,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DetectorSettingsResponse:
    """Update detector settings for an organization.

    Allows updating individual threshold values, which automatically
    sets the preset to 'custom'. Also supports enabling/disabling
    specific detectors.

    Requires admin or owner role.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify admin/owner
    await require_admin_or_owner(session, user, org)

    settings = await get_or_create_settings(session, org)

    # Update thresholds if provided
    if update.thresholds is not None:
        for key, value in update.thresholds.items():
            settings.update_threshold(key, value)

    # Update detector lists if provided
    if update.enabled_detectors is not None:
        settings.enabled_detectors = update.enabled_detectors

    if update.disabled_detectors is not None:
        settings.disabled_detectors = update.disabled_detectors

    await session.commit()

    logger.info(
        f"Updated detector settings for org {slug}",
        extra={
            "organization_slug": slug,
            "preset": settings.preset,
            "user_id": user.user_id,
        },
    )

    return DetectorSettingsResponse(
        preset=settings.preset,
        thresholds=settings.thresholds,
        enabled_detectors=settings.enabled_detectors,
        disabled_detectors=settings.disabled_detectors or [],
    )


@router.put(
    "/{slug}/settings/detectors/preset/{preset}",
    response_model=DetectorSettingsResponse,
    summary="Apply detector preset",
    description="Apply a preset configuration (strict, balanced, permissive).",
)
async def apply_detector_preset(
    slug: str,
    preset: str,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DetectorSettingsResponse:
    """Apply a preset configuration to detector settings.

    Available presets:
    - **strict**: Catch more issues, higher sensitivity, more findings
    - **balanced**: Default balanced thresholds (recommended)
    - **permissive**: Fewer findings, only critical/obvious issues

    Applying a preset replaces all threshold values with the preset defaults.
    Requires admin or owner role.
    """
    # Validate preset
    valid_presets = [p.value for p in DetectorPreset if p != DetectorPreset.CUSTOM]
    if preset not in valid_presets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid preset. Must be one of: {', '.join(valid_presets)}",
        )

    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify admin/owner
    await require_admin_or_owner(session, user, org)

    settings = await get_or_create_settings(session, org)
    settings.apply_preset(DetectorPreset(preset))

    await session.commit()

    logger.info(
        f"Applied detector preset '{preset}' for org {slug}",
        extra={
            "organization_slug": slug,
            "preset": preset,
            "user_id": user.user_id,
        },
    )

    return DetectorSettingsResponse(
        preset=settings.preset,
        thresholds=settings.thresholds,
        enabled_detectors=settings.enabled_detectors,
        disabled_detectors=settings.disabled_detectors or [],
    )


@router.get(
    "/{slug}/settings/detectors/presets",
    response_model=PresetListResponse,
    summary="List available presets",
    description="Get a list of all available detector presets with their configurations.",
)
async def list_detector_presets(
    slug: str,
    user: ClerkUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PresetListResponse:
    """List all available detector presets.

    Returns the configuration for each preset, allowing the UI to
    display what values each preset would apply.
    """
    org = await get_org_by_slug(session, slug)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    # Verify membership
    membership = await get_user_membership(session, user, org)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization",
        )

    presets = [
        {
            "name": DetectorPreset.STRICT.value,
            "display_name": "Strict",
            "description": "Catch more issues, higher sensitivity. More findings but may include more false positives.",
            "thresholds": PRESET_THRESHOLDS[DetectorPreset.STRICT.value],
        },
        {
            "name": DetectorPreset.BALANCED.value,
            "display_name": "Balanced",
            "description": "Default balanced thresholds. Recommended for most projects.",
            "thresholds": PRESET_THRESHOLDS[DetectorPreset.BALANCED.value],
        },
        {
            "name": DetectorPreset.PERMISSIVE.value,
            "display_name": "Permissive",
            "description": "Fewer findings, only critical and obvious issues. Best for legacy codebases.",
            "thresholds": PRESET_THRESHOLDS[DetectorPreset.PERMISSIVE.value],
        },
    ]

    return PresetListResponse(presets=presets)

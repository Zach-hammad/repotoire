"""Marketplace API schemas (Pydantic models).

This module defines request/response models for the marketplace API endpoints.
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import Generic, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from repotoire.db.models.marketplace import (
    AssetType,
    AssetVisibility,
    PricingType,
    PublisherType,
)


# =============================================================================
# Generic Pagination
# =============================================================================

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response wrapper."""

    items: list[T]
    total: int = Field(..., description="Total number of items matching the query")
    page: int = Field(..., ge=1, description="Current page number (1-indexed)")
    limit: int = Field(..., ge=1, le=100, description="Items per page")
    has_more: bool = Field(..., description="Whether more items exist after this page")


# =============================================================================
# Publisher Schemas
# =============================================================================


class PublisherBase(BaseModel):
    """Base fields for publisher."""

    slug: str = Field(
        ...,
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        description="URL-friendly identifier (lowercase letters, numbers, hyphens)",
        json_schema_extra={"example": "acme-corp"},
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Display name shown in marketplace",
        json_schema_extra={"example": "Acme Corporation"},
    )
    description: Optional[str] = Field(
        None,
        max_length=2000,
        description="Publisher bio/about text",
    )
    avatar_url: Optional[str] = Field(
        None,
        max_length=2048,
        description="Profile image URL",
    )
    website_url: Optional[str] = Field(
        None,
        max_length=2048,
        description="Publisher website",
    )
    github_url: Optional[str] = Field(
        None,
        max_length=2048,
        description="GitHub profile/org URL",
    )


class PublisherCreate(PublisherBase):
    """Request to create a publisher profile."""

    pass


class PublisherUpdate(BaseModel):
    """Request to update a publisher profile."""

    display_name: Optional[str] = Field(
        None,
        min_length=1,
        max_length=255,
    )
    description: Optional[str] = Field(None, max_length=2000)
    avatar_url: Optional[str] = Field(None, max_length=2048)
    website_url: Optional[str] = Field(None, max_length=2048)
    github_url: Optional[str] = Field(None, max_length=2048)


class PublisherResponse(PublisherBase):
    """Publisher response model."""

    id: UUID
    type: PublisherType
    verified_at: Optional[datetime] = None
    created_at: datetime
    asset_count: int = Field(
        default=0,
        description="Number of published assets",
    )

    model_config = ConfigDict(from_attributes=True)

    @property
    def is_verified(self) -> bool:
        """Check if the publisher is verified."""
        return self.verified_at is not None


# =============================================================================
# Asset Schemas
# =============================================================================


class AssetBase(BaseModel):
    """Base fields for assets."""

    slug: str = Field(
        ...,
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        description="URL-friendly identifier unique within publisher",
        json_schema_extra={"example": "code-review"},
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Display name",
        json_schema_extra={"example": "Code Review Skill"},
    )
    type: AssetType = Field(
        ...,
        description="Asset type",
    )
    description: Optional[str] = Field(
        None,
        max_length=500,
        description="Short description for cards/lists",
    )
    readme: Optional[str] = Field(
        None,
        max_length=50000,
        description="Full markdown documentation",
    )
    icon_url: Optional[str] = Field(
        None,
        max_length=2048,
        description="Asset icon/logo URL",
    )
    tags: Optional[list[str]] = Field(
        None,
        max_length=20,
        description="Tags for categorization",
    )
    pricing_type: PricingType = Field(
        default=PricingType.FREE,
        description="Pricing model",
    )
    price_cents: Optional[int] = Field(
        None,
        ge=0,
        description="Price in cents (required for paid assets)",
    )
    visibility: AssetVisibility = Field(
        default=AssetVisibility.PUBLIC,
        description="Visibility level",
    )
    metadata: Optional[dict] = Field(
        None,
        description="Flexible metadata (e.g., required permissions)",
    )

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        """Validate tags."""
        if v is None:
            return v
        # Normalize tags: lowercase, strip whitespace
        normalized = [tag.lower().strip() for tag in v if tag.strip()]
        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for tag in normalized:
            if tag not in seen:
                seen.add(tag)
                unique.append(tag)
        return unique

    @field_validator("price_cents")
    @classmethod
    def validate_price(cls, v: Optional[int], info) -> Optional[int]:
        """Validate price is set for paid assets."""
        # Note: Full validation happens in service layer
        return v


class AssetCreate(AssetBase):
    """Request to create an asset."""

    pass


class AssetUpdate(BaseModel):
    """Request to update an asset."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    readme: Optional[str] = Field(None, max_length=50000)
    icon_url: Optional[str] = Field(None, max_length=2048)
    tags: Optional[list[str]] = Field(None, max_length=20)
    pricing_type: Optional[PricingType] = None
    price_cents: Optional[int] = Field(None, ge=0)
    visibility: Optional[AssetVisibility] = None
    metadata: Optional[dict] = None


class AssetListItem(BaseModel):
    """Asset summary for list views."""

    id: UUID
    publisher_slug: str
    publisher_display_name: str
    publisher_verified: bool
    slug: str
    name: str
    type: AssetType
    description: Optional[str]
    icon_url: Optional[str]
    tags: Optional[list[str]]
    pricing_type: PricingType
    price_cents: Optional[int]
    visibility: AssetVisibility
    install_count: int
    rating_avg: Optional[Decimal]
    rating_count: int
    published_at: Optional[datetime]
    featured_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)

    @property
    def is_featured(self) -> bool:
        """Check if the asset is featured."""
        return self.featured_at is not None


class AssetDetail(AssetListItem):
    """Full asset details."""

    readme: Optional[str]
    metadata: Optional[dict]
    deprecated_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    latest_version: Optional[str] = None


class AssetSearchParams(BaseModel):
    """Search parameters for assets."""

    query: Optional[str] = Field(
        None,
        max_length=200,
        description="Full-text search query",
    )
    type: Optional[AssetType] = Field(
        None,
        description="Filter by asset type",
    )
    tags: Optional[list[str]] = Field(
        None,
        description="Filter by tags (OR logic)",
    )
    pricing_type: Optional[PricingType] = Field(
        None,
        description="Filter by pricing type",
    )
    publisher: Optional[str] = Field(
        None,
        description="Filter by publisher slug",
    )
    featured_only: bool = Field(
        default=False,
        description="Only return featured assets",
    )
    sort_by: str = Field(
        default="popular",
        description="Sort order: popular, recent, rating, name",
    )
    page: int = Field(default=1, ge=1, description="Page number")
    limit: int = Field(default=20, ge=1, le=100, description="Items per page")


# =============================================================================
# Version Schemas
# =============================================================================


class VersionBase(BaseModel):
    """Base fields for versions."""

    version: str = Field(
        ...,
        min_length=1,
        max_length=50,
        pattern=r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$",
        description="Semantic version string (e.g., 1.0.0, 2.0.0-beta.1)",
        json_schema_extra={"example": "1.0.0"},
    )
    changelog: Optional[str] = Field(
        None,
        max_length=10000,
        description="What changed in this version",
    )
    content: dict = Field(
        ...,
        description="The actual asset content (JSON)",
    )
    source_url: Optional[str] = Field(
        None,
        max_length=2048,
        description="Link to source (e.g., GitHub)",
    )
    min_repotoire_version: Optional[str] = Field(
        None,
        max_length=20,
        description="Minimum compatible Repotoire version",
    )
    max_repotoire_version: Optional[str] = Field(
        None,
        max_length=20,
        description="Maximum compatible Repotoire version",
    )


class VersionCreate(VersionBase):
    """Request to create a new version."""

    publish: bool = Field(
        default=False,
        description="Immediately publish this version",
    )


class VersionResponse(BaseModel):
    """Version response model."""

    id: UUID
    asset_id: UUID
    version: str
    changelog: Optional[str]
    source_url: Optional[str]
    checksum: str
    min_repotoire_version: Optional[str]
    max_repotoire_version: Optional[str]
    download_count: int
    published_at: Optional[datetime]
    yanked_at: Optional[datetime]
    yank_reason: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @property
    def is_published(self) -> bool:
        """Check if the version is published."""
        return self.published_at is not None

    @property
    def is_yanked(self) -> bool:
        """Check if the version is yanked."""
        return self.yanked_at is not None


class VersionDetailResponse(VersionResponse):
    """Version response with content included (for sync)."""

    content: dict


# =============================================================================
# Install Schemas
# =============================================================================


class InstallRequest(BaseModel):
    """Request to install an asset."""

    version: Optional[str] = Field(
        None,
        description="Specific version to install (defaults to latest)",
    )
    config: Optional[dict] = Field(
        None,
        description="Custom configuration for this installation",
    )
    auto_update: bool = Field(
        default=True,
        description="Automatically update to new versions",
    )


class InstallResponse(BaseModel):
    """Installation response model."""

    id: UUID
    user_id: str
    asset_id: UUID
    version_id: Optional[UUID]
    version_string: Optional[str] = None
    config: Optional[dict]
    enabled: bool
    auto_update: bool
    created_at: datetime
    updated_at: datetime
    # Include asset info for convenience
    asset: AssetListItem

    model_config = ConfigDict(from_attributes=True)


class InstallUpdateRequest(BaseModel):
    """Request to update an installation."""

    version: Optional[str] = Field(
        None,
        description="Update to a specific version",
    )
    config: Optional[dict] = Field(
        None,
        description="Update configuration",
    )
    enabled: Optional[bool] = Field(
        None,
        description="Enable/disable the installation",
    )
    auto_update: Optional[bool] = Field(
        None,
        description="Enable/disable auto-updates",
    )


class SyncResponse(BaseModel):
    """Response for syncing installed assets (returns full content)."""

    installations: list["InstallWithContent"]
    synced_at: datetime


class InstallWithContent(BaseModel):
    """Installation with full asset content for local sync."""

    id: UUID
    asset_id: UUID
    publisher_slug: str
    asset_slug: str
    asset_type: AssetType
    version: str
    content: dict
    config: Optional[dict]
    enabled: bool
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Review Schemas
# =============================================================================


class ReviewBase(BaseModel):
    """Base fields for reviews."""

    rating: int = Field(
        ...,
        ge=1,
        le=5,
        description="Rating 1-5 stars",
    )
    title: Optional[str] = Field(
        None,
        max_length=255,
        description="Review title",
    )
    body: Optional[str] = Field(
        None,
        max_length=5000,
        description="Review body text",
    )


class ReviewCreate(ReviewBase):
    """Request to create a review."""

    pass


class ReviewUpdate(BaseModel):
    """Request to update a review."""

    rating: Optional[int] = Field(None, ge=1, le=5)
    title: Optional[str] = Field(None, max_length=255)
    body: Optional[str] = Field(None, max_length=5000)


class ReviewResponse(BaseModel):
    """Review response model."""

    id: UUID
    user_id: str
    asset_id: UUID
    rating: int
    title: Optional[str]
    body: Optional[str]
    helpful_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewListResponse(BaseModel):
    """Paginated review list response."""

    reviews: list[ReviewResponse]
    total: int
    page: int
    limit: int
    has_more: bool
    rating_summary: "RatingSummary"


class RatingSummary(BaseModel):
    """Summary of ratings for an asset."""

    average: Optional[Decimal] = Field(None, description="Average rating (1-5)")
    count: int = Field(..., description="Total number of reviews")
    distribution: dict[int, int] = Field(
        ...,
        description="Count per rating (1-5)",
        json_schema_extra={"example": {"1": 2, "2": 5, "3": 10, "4": 25, "5": 58}},
    )


# =============================================================================
# Org Private Asset Schemas
# =============================================================================


class OrgAssetBase(BaseModel):
    """Base fields for org-private assets."""

    slug: str = Field(
        ...,
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        description="URL-friendly identifier unique within org",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Display name",
    )
    type: AssetType = Field(
        ...,
        description="Asset type",
    )
    description: Optional[str] = Field(
        None,
        max_length=500,
        description="Short description",
    )
    content: dict = Field(
        ...,
        description="The actual asset content (JSON)",
    )
    config_schema: Optional[dict] = Field(
        None,
        description="JSON Schema for configuration",
    )


class OrgAssetCreate(OrgAssetBase):
    """Request to create an org-private asset."""

    pass


class OrgAssetUpdate(BaseModel):
    """Request to update an org-private asset."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    content: Optional[dict] = None
    config_schema: Optional[dict] = None
    enabled: Optional[bool] = None


class OrgAssetResponse(BaseModel):
    """Org-private asset response model."""

    id: UUID
    org_id: str
    type: AssetType
    slug: str
    name: str
    description: Optional[str]
    content: dict
    config_schema: Optional[dict]
    created_by_user_id: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Category/Discovery Schemas
# =============================================================================


class CategoryInfo(BaseModel):
    """Category information for discovery."""

    type: AssetType
    display_name: str
    description: str
    asset_count: int
    icon: str


class CategoriesResponse(BaseModel):
    """Response for categories endpoint."""

    categories: list[CategoryInfo]


# =============================================================================
# Stripe Connect Schemas
# =============================================================================


class ConnectAccountRequest(BaseModel):
    """Request to create/start Stripe Connect onboarding."""

    country: str = Field(
        default="US",
        min_length=2,
        max_length=2,
        description="Two-letter country code",
    )


class ConnectAccountResponse(BaseModel):
    """Response for Stripe Connect account creation."""

    account_id: str = Field(..., description="Stripe Connect account ID")
    onboarding_url: str = Field(..., description="URL to complete onboarding")


class ConnectStatusResponse(BaseModel):
    """Response for Stripe Connect account status."""

    connected: bool = Field(..., description="Whether account is connected")
    charges_enabled: bool = Field(
        default=False,
        description="Whether account can accept charges",
    )
    payouts_enabled: bool = Field(
        default=False,
        description="Whether account can receive payouts",
    )
    onboarding_complete: bool = Field(
        default=False,
        description="Whether onboarding is complete",
    )
    requirements: Optional[dict] = Field(
        None,
        description="Pending requirements if any",
    )
    dashboard_url: Optional[str] = Field(
        None,
        description="URL to Stripe Express dashboard (if connected)",
    )


class ConnectBalanceResponse(BaseModel):
    """Response for creator's balance and payouts."""

    balance: dict = Field(
        ...,
        description="Current balance (available and pending)",
    )
    recent_payouts: list[dict] = Field(
        default_factory=list,
        description="Recent payout history",
    )


class PurchaseRequest(BaseModel):
    """Request to purchase a paid asset."""

    pass  # No body needed, asset determined by URL path


class PurchaseResponse(BaseModel):
    """Response for purchase initiation."""

    client_secret: str = Field(
        ...,
        description="Stripe PaymentIntent client_secret for frontend",
    )
    payment_intent_id: str = Field(
        ...,
        description="Stripe PaymentIntent ID",
    )
    amount_cents: int = Field(..., description="Total amount in cents")
    currency: str = Field(default="usd", description="Currency code")
    platform_fee_cents: int = Field(..., description="Platform fee (15%)")
    creator_share_cents: int = Field(..., description="Creator share (85%)")


class PurchaseStatusResponse(BaseModel):
    """Response for purchase status check."""

    purchased: bool = Field(..., description="Whether asset has been purchased")
    purchase_id: Optional[UUID] = Field(None, description="Purchase ID if purchased")
    purchased_at: Optional[datetime] = Field(None, description="Purchase timestamp")


# =============================================================================
# Dependency Resolution Schemas
# =============================================================================


class DependencyResolveRequest(BaseModel):
    """Request to resolve dependencies."""

    dependencies: dict[str, str] = Field(
        ...,
        description='Map of asset slug to version constraint (e.g., {"@pub/name": "^1.0.0"})',
        examples=[{"@repotoire/security-scanner": "^1.0.0", "@myorg/helper": "~2.1.0"}],
    )
    include_dev: bool = Field(
        default=False,
        description="Whether to include dev dependencies",
    )


class ResolvedDependencyResponse(BaseModel):
    """A resolved dependency with version and download URL."""

    slug: str = Field(..., description="Asset slug (e.g., @publisher/name)")
    version: str = Field(..., description="Resolved version")
    download_url: str = Field(..., description="URL to download the asset")
    integrity: str = Field(default="", description="SHA256 integrity hash")


class DependencyResolveResponse(BaseModel):
    """Response for dependency resolution."""

    resolved: list[ResolvedDependencyResponse] = Field(
        ...,
        description="Flat list of resolved dependencies",
    )
    lockfile: Optional[dict] = Field(
        None,
        description="Generated lockfile content for reproducible installs",
    )


class UpdateAvailableResponse(BaseModel):
    """Information about an available update."""

    slug: str = Field(..., description="Asset slug")
    current: str = Field(..., description="Currently installed version")
    latest: str = Field(..., description="Latest available version")
    update_type: str = Field(..., description="Type: major, minor, or patch")
    changelog: Optional[str] = Field(None, description="Changelog for the update")


class OutdatedResponse(BaseModel):
    """Response for checking outdated packages."""

    updates: list[UpdateAvailableResponse] = Field(
        default_factory=list,
        description="List of available updates",
    )
    total_installed: int = Field(..., description="Total installed packages checked")
    total_outdated: int = Field(..., description="Number of packages with updates")


# =============================================================================
# Tag Schemas
# =============================================================================


class TagCount(BaseModel):
    """Tag with count of assets using it."""

    tag: str = Field(..., description="Tag name")
    count: int = Field(..., description="Number of assets with this tag")


class TagsResponse(BaseModel):
    """Response for tags endpoint."""

    tags: list[TagCount] = Field(..., description="Tags with counts, ordered by count desc")


# =============================================================================
# Browse Response (same as PaginatedResponse[AssetListItem] but explicit)
# =============================================================================


class BrowseResponse(PaginatedResponse[AssetListItem]):
    """Response for browse endpoint."""

    pass


class FeaturedResponse(BaseModel):
    """Response for featured assets endpoint."""

    assets: list[AssetListItem] = Field(..., description="Featured assets (max 6)")


# =============================================================================
# Analytics Schemas
# =============================================================================


class EventType(str, enum.Enum):
    """Type of analytics event."""

    DOWNLOAD = "download"
    INSTALL = "install"
    UNINSTALL = "uninstall"
    UPDATE = "update"


class TrackEventRequest(BaseModel):
    """Request to track an analytics event."""

    event_type: EventType = Field(..., description="Type of event")
    asset_version_id: Optional[UUID] = Field(
        None, description="Specific version ID if applicable"
    )
    cli_version: Optional[str] = Field(
        None, max_length=50, description="CLI version"
    )
    os_platform: Optional[str] = Field(
        None, max_length=50, description="OS platform (darwin, linux, win32)"
    )
    source: Optional[str] = Field(
        default="api", max_length=50, description="Event source (cli, web, api)"
    )
    metadata: Optional[dict] = Field(
        None, description="Additional context"
    )


class TrackEventResponse(BaseModel):
    """Response for event tracking."""

    success: bool = Field(..., description="Whether event was tracked")
    event_id: UUID = Field(..., description="ID of the created event")


class DailyStatsItem(BaseModel):
    """Daily statistics for a single day."""

    date: datetime = Field(..., description="The date")
    downloads: int = Field(default=0, description="Downloads on this day")
    installs: int = Field(default=0, description="Installs on this day")
    uninstalls: int = Field(default=0, description="Uninstalls on this day")
    updates: int = Field(default=0, description="Updates on this day")
    revenue_cents: int = Field(default=0, description="Revenue in cents")
    unique_users: int = Field(default=0, description="Unique users")


class AssetStatsResponse(BaseModel):
    """Response with asset statistics."""

    asset_id: UUID = Field(..., description="Asset ID")
    total_downloads: int = Field(default=0, description="Lifetime downloads")
    total_installs: int = Field(default=0, description="Lifetime installs")
    total_uninstalls: int = Field(default=0, description="Lifetime uninstalls")
    total_updates: int = Field(default=0, description="Lifetime updates")
    active_installs: int = Field(default=0, description="Current active installs")
    rating_avg: Optional[Decimal] = Field(None, description="Average rating")
    rating_count: int = Field(default=0, description="Total reviews")
    total_revenue_cents: int = Field(default=0, description="Lifetime revenue in cents")
    total_purchases: int = Field(default=0, description="Lifetime purchases")
    downloads_7d: int = Field(default=0, description="Downloads in last 7 days")
    downloads_30d: int = Field(default=0, description="Downloads in last 30 days")
    installs_7d: int = Field(default=0, description="Installs in last 7 days")
    installs_30d: int = Field(default=0, description="Installs in last 30 days")


class AssetTrendsResponse(BaseModel):
    """Response with asset trend data."""

    asset_id: UUID = Field(..., description="Asset ID")
    period_days: int = Field(..., description="Number of days in period")
    daily_stats: list[DailyStatsItem] = Field(
        default_factory=list, description="Daily breakdown"
    )
    total_downloads: int = Field(default=0, description="Total downloads in period")
    total_installs: int = Field(default=0, description="Total installs in period")
    total_uninstalls: int = Field(default=0, description="Total uninstalls in period")
    total_revenue_cents: int = Field(default=0, description="Total revenue in period")
    avg_daily_downloads: float = Field(default=0.0, description="Average daily downloads")
    avg_daily_installs: float = Field(default=0.0, description="Average daily installs")


class AssetStatsWithName(AssetStatsResponse):
    """Asset stats with name/slug for lists."""

    name: Optional[str] = Field(None, description="Asset name")
    slug: Optional[str] = Field(None, description="Asset slug")


class CreatorStatsResponse(BaseModel):
    """Response with creator/publisher statistics."""

    publisher_id: UUID = Field(..., description="Publisher ID")
    total_assets: int = Field(default=0, description="Number of assets")
    total_downloads: int = Field(default=0, description="Total downloads across all assets")
    total_installs: int = Field(default=0, description="Total installs across all assets")
    total_active_installs: int = Field(
        default=0, description="Total active installs"
    )
    total_revenue_cents: int = Field(default=0, description="Total revenue in cents")
    avg_rating: Optional[Decimal] = Field(None, description="Average rating across assets")
    total_reviews: int = Field(default=0, description="Total reviews")
    downloads_7d: int = Field(default=0, description="Downloads in last 7 days")
    downloads_30d: int = Field(default=0, description="Downloads in last 30 days")
    assets: list[AssetStatsWithName] = Field(
        default_factory=list, description="Per-asset stats"
    )


class TopAssetItem(BaseModel):
    """Item in a top assets list."""

    id: str = Field(..., description="Asset ID")
    name: str = Field(..., description="Asset name")
    slug: str = Field(..., description="Asset slug")
    publisher_slug: str = Field(..., description="Publisher slug")
    value: int = Field(..., description="The metric value")


class PlatformStatsResponse(BaseModel):
    """Response with platform-wide analytics (admin only)."""

    total_assets: int = Field(default=0, description="Total marketplace assets")
    total_publishers: int = Field(default=0, description="Total publishers")
    total_downloads: int = Field(default=0, description="Total downloads")
    total_installs: int = Field(default=0, description="Total installs")
    total_active_installs: int = Field(default=0, description="Total active installs")
    total_revenue_cents: int = Field(default=0, description="Total revenue in cents")
    downloads_7d: int = Field(default=0, description="Downloads in last 7 days")
    downloads_30d: int = Field(default=0, description="Downloads in last 30 days")
    installs_7d: int = Field(default=0, description="Installs in last 7 days")
    installs_30d: int = Field(default=0, description="Installs in last 30 days")
    top_by_downloads: list[TopAssetItem] = Field(
        default_factory=list, description="Top assets by downloads"
    )
    top_by_installs: list[TopAssetItem] = Field(
        default_factory=list, description="Top assets by active installs"
    )
    top_by_revenue: list[TopAssetItem] = Field(
        default_factory=list, description="Top assets by revenue"
    )


# Update forward references
SyncResponse.model_rebuild()
ReviewListResponse.model_rebuild()

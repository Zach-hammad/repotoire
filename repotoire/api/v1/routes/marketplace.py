"""Marketplace API routes.

This module provides endpoints for the Repotoire Marketplace:
- Discovery & Browse (list, search, categories)
- User Installations (install, uninstall, sync)
- Reviews & Ratings (CRUD)
- Publisher Management (profile CRUD)
- Asset Publishing (create, update, version, publish)
- Private Org Assets (org-scoped CRUD)
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.api.services import marketplace as mp_service
from repotoire.api.shared.auth import (
    ClerkUser,
    get_current_user,
    get_current_user_or_api_key,
    get_optional_user,
    require_org,
)
from repotoire.api.v1.schemas.marketplace import (
    AssetCreate,
    AssetDetail,
    AssetListItem,
    AssetSearchParams,
    AssetStatsResponse,
    AssetStatsWithName,
    AssetTrendsResponse,
    AssetUpdate,
    BrowseResponse,
    CategoriesResponse,
    CategoryInfo,
    ConnectAccountRequest,
    ConnectAccountResponse,
    ConnectBalanceResponse,
    ConnectStatusResponse,
    CreatorStatsResponse,
    DailyStatsItem,
    DependencyResolveRequest,
    DependencyResolveResponse,
    FeaturedResponse,
    InstallRequest,
    InstallResponse,
    InstallUpdateRequest,
    InstallWithContent,
    OrgAssetCreate,
    OrgAssetResponse,
    OrgAssetUpdate,
    OutdatedResponse,
    PaginatedResponse,
    PlatformStatsResponse,
    PublisherCreate,
    PublisherResponse,
    PublisherUpdate,
    PurchaseResponse,
    PurchaseStatusResponse,
    RatingSummary,
    ResolvedDependencyResponse,
    ReviewCreate,
    ReviewListResponse,
    ReviewResponse,
    ReviewUpdate,
    SyncResponse,
    TagCount,
    TagsResponse,
    TopAssetItem,
    TrackEventRequest,
    TrackEventResponse,
    UpdateAvailableResponse,
    VersionCreate,
    VersionDetailResponse,
    VersionResponse,
)
from repotoire.db.models.marketplace import AssetType, AssetVisibility, PricingType
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


# =============================================================================
# Discovery & Browse Endpoints
# =============================================================================


@router.get(
    "/assets",
    response_model=PaginatedResponse[AssetListItem],
    summary="Search and browse assets",
    description="""
Search and filter marketplace assets.

**Public endpoint** - no authentication required.

Supports:
- Full-text search across name and description
- Filtering by type, tags, pricing, publisher
- Sorting by popularity, recency, rating, or name
- Pagination with configurable page size

Assets are returned with publisher information and statistics.
    """,
)
async def list_assets(
    query: Optional[str] = Query(None, max_length=200, description="Search query"),
    type: Optional[AssetType] = Query(None, description="Filter by asset type"),
    tags: Optional[list[str]] = Query(None, description="Filter by tags"),
    pricing_type: Optional[PricingType] = Query(None, description="Filter by pricing"),
    publisher: Optional[str] = Query(None, description="Filter by publisher slug"),
    featured: bool = Query(False, description="Only return featured assets"),
    sort: str = Query("popular", description="Sort by: popular, recent, rating, name"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AssetListItem]:
    """List and search marketplace assets."""
    result = await mp_service.search_assets(
        db=db,
        query=query,
        asset_type=type,
        tags=tags,
        pricing_type=pricing_type,
        publisher_slug=publisher,
        featured_only=featured,
        sort_by=sort,
        page=page,
        limit=limit,
    )

    items = [
        AssetListItem(
            id=asset.id,
            publisher_slug=asset.publisher.slug,
            publisher_display_name=asset.publisher.display_name,
            publisher_verified=asset.publisher.is_verified,
            slug=asset.slug,
            name=asset.name,
            type=AssetType(asset.type),
            description=asset.description,
            icon_url=asset.icon_url,
            tags=asset.tags,
            pricing_type=PricingType(asset.pricing_type),
            price_cents=asset.price_cents,
            visibility=AssetVisibility(asset.visibility),
            install_count=asset.install_count,
            rating_avg=asset.rating_avg,
            rating_count=asset.rating_count,
            published_at=asset.published_at,
            featured_at=asset.featured_at,
        )
        for asset in result.items
    ]

    return PaginatedResponse(
        items=items,
        total=result.total,
        page=result.page,
        limit=result.limit,
        has_more=result.has_more,
    )


@router.get(
    "/browse",
    response_model=BrowseResponse,
    summary="Browse marketplace assets",
    description="""
Browse and search marketplace assets with filtering and sorting.

**Public endpoint** - no authentication required.

This is the primary discovery endpoint. Supports:
- Full-text search across name and description
- Filtering by type, tags, pricing, publisher, verification status
- Sorting by popularity, recency, rating, or name
- Pagination with configurable page size

Use this endpoint for the main marketplace browse page.
    """,
)
async def browse_marketplace(
    query: Optional[str] = Query(None, max_length=200, description="Search query"),
    type: Optional[AssetType] = Query(None, description="Filter by asset type"),
    tags: Optional[list[str]] = Query(None, description="Filter by tags"),
    pricing: Optional[PricingType] = Query(None, alias="pricing", description="Filter by pricing type"),
    source: Optional[str] = Query(None, description="Filter by publisher slug"),
    sort: str = Query("popular", description="Sort by: popular, recent, rating, name"),
    verified_only: bool = Query(False, description="Only return assets from verified publishers"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, alias="page_size", description="Items per page"),
    db: AsyncSession = Depends(get_db),
) -> BrowseResponse:
    """Browse marketplace assets with filtering and sorting."""
    result = await mp_service.search_assets(
        db=db,
        query=query,
        asset_type=type,
        tags=tags,
        pricing_type=pricing,
        publisher_slug=source,
        featured_only=False,
        sort_by=sort,
        page=page,
        limit=page_size,
    )

    items = [
        AssetListItem(
            id=asset.id,
            publisher_slug=asset.publisher.slug,
            publisher_display_name=asset.publisher.display_name,
            publisher_verified=asset.publisher.is_verified,
            slug=asset.slug,
            name=asset.name,
            type=AssetType(asset.type),
            description=asset.description,
            icon_url=asset.icon_url,
            tags=asset.tags,
            pricing_type=PricingType(asset.pricing_type),
            price_cents=asset.price_cents,
            visibility=AssetVisibility(asset.visibility),
            install_count=asset.install_count,
            rating_avg=asset.rating_avg,
            rating_count=asset.rating_count,
            published_at=asset.published_at,
            featured_at=asset.featured_at,
        )
        for asset in result.items
        # Filter by verified if requested
        if not verified_only or asset.publisher.is_verified
    ]

    return BrowseResponse(
        items=items,
        total=result.total,
        page=result.page,
        limit=result.limit,
        has_more=result.has_more,
    )


@router.get(
    "/featured",
    response_model=FeaturedResponse,
    summary="Get featured assets",
    description="""
Get featured marketplace assets for homepage display.

**Public endpoint** - no authentication required.

Returns up to 6 featured assets, ordered by when they were featured.
Featured assets are manually curated by admins.
    """,
)
async def get_featured_assets(
    db: AsyncSession = Depends(get_db),
) -> FeaturedResponse:
    """Get featured marketplace assets."""
    assets = await mp_service.get_featured_assets(db, limit=6)

    items = [
        AssetListItem(
            id=asset.id,
            publisher_slug=asset.publisher.slug,
            publisher_display_name=asset.publisher.display_name,
            publisher_verified=asset.publisher.is_verified,
            slug=asset.slug,
            name=asset.name,
            type=AssetType(asset.type),
            description=asset.description,
            icon_url=asset.icon_url,
            tags=asset.tags,
            pricing_type=PricingType(asset.pricing_type),
            price_cents=asset.price_cents,
            visibility=AssetVisibility(asset.visibility),
            install_count=asset.install_count,
            rating_avg=asset.rating_avg,
            rating_count=asset.rating_count,
            published_at=asset.published_at,
            featured_at=asset.featured_at,
        )
        for asset in assets
    ]

    return FeaturedResponse(assets=items)


@router.get(
    "/tags",
    response_model=TagsResponse,
    summary="Get asset tags",
    description="""
Get aggregated tag counts across all published assets.

**Public endpoint** - no authentication required.

Returns tags ordered by count (most popular first).
Useful for tag cloud displays and filter suggestions.
    """,
)
async def get_tags(
    limit: int = Query(50, ge=1, le=200, description="Maximum number of tags to return"),
    db: AsyncSession = Depends(get_db),
) -> TagsResponse:
    """Get aggregated tag counts."""
    tag_counts = await mp_service.get_tags_with_counts(db, limit=limit)

    return TagsResponse(
        tags=[TagCount(tag=tc["tag"], count=tc["count"]) for tc in tag_counts]
    )


@router.get(
    "/assets/{publisher_slug}/{asset_slug}",
    response_model=AssetDetail,
    summary="Get asset details",
    description="""
Get full details for a specific asset.

**Public endpoint** - no authentication required.

Returns complete asset information including:
- Full readme/documentation
- Metadata and configuration
- Publisher information
- Statistics (installs, ratings)
- Latest version string
    """,
)
async def get_asset(
    publisher_slug: str,
    asset_slug: str,
    db: AsyncSession = Depends(get_db),
) -> AssetDetail:
    """Get asset details by publisher and asset slug."""
    asset = await mp_service.get_asset_by_publisher_slug(
        db, publisher_slug, asset_slug, include_versions=True
    )
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Get latest version string
    latest_version = asset.latest_version
    latest_version_str = latest_version.version if latest_version else None

    return AssetDetail(
        id=asset.id,
        publisher_slug=asset.publisher.slug,
        publisher_display_name=asset.publisher.display_name,
        publisher_verified=asset.publisher.is_verified,
        slug=asset.slug,
        name=asset.name,
        type=AssetType(asset.type),
        description=asset.description,
        readme=asset.readme,
        icon_url=asset.icon_url,
        tags=asset.tags,
        pricing_type=PricingType(asset.pricing_type),
        price_cents=asset.price_cents,
        visibility=AssetVisibility(asset.visibility),
        install_count=asset.install_count,
        rating_avg=asset.rating_avg,
        rating_count=asset.rating_count,
        published_at=asset.published_at,
        featured_at=asset.featured_at,
        deprecated_at=asset.deprecated_at,
        metadata=asset.asset_metadata,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        latest_version=latest_version_str,
    )


@router.get(
    "/categories",
    response_model=CategoriesResponse,
    summary="Get asset categories",
    description="""
Get list of asset categories with counts.

**Public endpoint** - no authentication required.

Returns all asset types with:
- Display name and description
- Icon identifier
- Count of published assets
    """,
)
async def get_categories(
    db: AsyncSession = Depends(get_db),
) -> CategoriesResponse:
    """Get asset categories with counts."""
    categories = await mp_service.get_categories_with_counts(db)

    return CategoriesResponse(
        categories=[
            CategoryInfo(
                type=cat["type"],
                display_name=cat["display_name"],
                description=cat["description"],
                asset_count=cat["asset_count"],
                icon=cat["icon"],
            )
            for cat in categories
        ]
    )


# =============================================================================
# User Installation Endpoints
# =============================================================================


@router.get(
    "/installed",
    response_model=list[InstallResponse],
    summary="List installed assets",
    description="""
Get all assets installed by the current user.

**Requires authentication.**

Returns installations with:
- Asset details (name, type, publisher)
- Installed version
- Custom configuration
- Enable/auto-update status
    """,
)
async def list_installed(
    enabled_only: bool = Query(False, description="Only return enabled installations"),
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> list[InstallResponse]:
    """List user's installed assets."""
    installs = await mp_service.get_user_installs(db, user.user_id, enabled_only)

    return [
        InstallResponse(
            id=install.id,
            user_id=install.user_id,
            asset_id=install.asset_id,
            version_id=install.version_id,
            version_string=install.version.version if install.version else None,
            config=install.config,
            enabled=install.enabled,
            auto_update=install.auto_update,
            created_at=install.created_at,
            updated_at=install.updated_at,
            asset=AssetListItem(
                id=install.asset.id,
                publisher_slug=install.asset.publisher.slug,
                publisher_display_name=install.asset.publisher.display_name,
                publisher_verified=install.asset.publisher.is_verified,
                slug=install.asset.slug,
                name=install.asset.name,
                type=AssetType(install.asset.type),
                description=install.asset.description,
                icon_url=install.asset.icon_url,
                tags=install.asset.tags,
                pricing_type=PricingType(install.asset.pricing_type),
                price_cents=install.asset.price_cents,
                visibility=AssetVisibility(install.asset.visibility),
                install_count=install.asset.install_count,
                rating_avg=install.asset.rating_avg,
                rating_count=install.asset.rating_count,
                published_at=install.asset.published_at,
                featured_at=install.asset.featured_at,
            ),
        )
        for install in installs
    ]


@router.post(
    "/install/{publisher_slug}/{asset_slug}",
    response_model=InstallResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Install an asset",
    description="""
Install a marketplace asset.

**Requires authentication.**

Optionally specify:
- Version (defaults to latest)
- Custom configuration
- Auto-update preference

If already installed, updates the installation.
    """,
)
async def install_asset(
    publisher_slug: str,
    asset_slug: str,
    request: InstallRequest,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> InstallResponse:
    """Install an asset."""
    asset = await mp_service.get_asset_by_publisher_slug(
        db, publisher_slug, asset_slug, include_versions=True
    )
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Get specific version if requested
    version = None
    if request.version:
        version = await mp_service.get_version_by_string(db, asset.id, request.version)
        if not version:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version '{request.version}' not found",
            )

    install = await mp_service.install_asset(
        db=db,
        user_id=user.user_id,
        asset=asset,
        version=version,
        config=request.config,
        auto_update=request.auto_update,
    )
    await db.commit()

    return InstallResponse(
        id=install.id,
        user_id=install.user_id,
        asset_id=install.asset_id,
        version_id=install.version_id,
        version_string=install.version.version if install.version else None,
        config=install.config,
        enabled=install.enabled,
        auto_update=install.auto_update,
        created_at=install.created_at,
        updated_at=install.updated_at,
        asset=AssetListItem(
            id=install.asset.id,
            publisher_slug=install.asset.publisher.slug,
            publisher_display_name=install.asset.publisher.display_name,
            publisher_verified=install.asset.publisher.is_verified,
            slug=install.asset.slug,
            name=install.asset.name,
            type=AssetType(install.asset.type),
            description=install.asset.description,
            icon_url=install.asset.icon_url,
            tags=install.asset.tags,
            pricing_type=PricingType(install.asset.pricing_type),
            price_cents=install.asset.price_cents,
            visibility=AssetVisibility(install.asset.visibility),
            install_count=install.asset.install_count,
            rating_avg=install.asset.rating_avg,
            rating_count=install.asset.rating_count,
            published_at=install.asset.published_at,
            featured_at=install.asset.featured_at,
        ),
    )


@router.delete(
    "/install/{publisher_slug}/{asset_slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Uninstall an asset",
    description="""
Uninstall a marketplace asset.

**Requires authentication.**

Removes the installation and decrements the asset's install count.
    """,
)
async def uninstall_asset(
    publisher_slug: str,
    asset_slug: str,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Uninstall an asset."""
    asset = await mp_service.get_asset_by_publisher_slug(db, publisher_slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    removed = await mp_service.uninstall_asset(db, user.user_id, asset)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not installed",
        )

    await db.commit()


@router.patch(
    "/install/{publisher_slug}/{asset_slug}",
    response_model=InstallResponse,
    summary="Update installation",
    description="""
Update an installation's configuration.

**Requires authentication.**

Can update:
- Version (upgrade/downgrade)
- Custom configuration
- Enable/disable status
- Auto-update preference
    """,
)
async def update_installation(
    publisher_slug: str,
    asset_slug: str,
    request: InstallUpdateRequest,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> InstallResponse:
    """Update an installation."""
    asset = await mp_service.get_asset_by_publisher_slug(db, publisher_slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    install = await mp_service.get_install(db, user.user_id, asset.id)
    if not install:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not installed",
        )

    # Get specific version if requested
    version = None
    if request.version:
        version = await mp_service.get_version_by_string(db, asset.id, request.version)
        if not version:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version '{request.version}' not found",
            )

    install = await mp_service.update_install(
        db=db,
        install=install,
        version=version,
        config=request.config,
        enabled=request.enabled,
        auto_update=request.auto_update,
    )
    await db.commit()

    return InstallResponse(
        id=install.id,
        user_id=install.user_id,
        asset_id=install.asset_id,
        version_id=install.version_id,
        version_string=install.version.version if install.version else None,
        config=install.config,
        enabled=install.enabled,
        auto_update=install.auto_update,
        created_at=install.created_at,
        updated_at=install.updated_at,
        asset=AssetListItem(
            id=install.asset.id,
            publisher_slug=install.asset.publisher.slug,
            publisher_display_name=install.asset.publisher.display_name,
            publisher_verified=install.asset.publisher.is_verified,
            slug=install.asset.slug,
            name=install.asset.name,
            type=AssetType(install.asset.type),
            description=install.asset.description,
            icon_url=install.asset.icon_url,
            tags=install.asset.tags,
            pricing_type=PricingType(install.asset.pricing_type),
            price_cents=install.asset.price_cents,
            visibility=AssetVisibility(install.asset.visibility),
            install_count=install.asset.install_count,
            rating_avg=install.asset.rating_avg,
            rating_count=install.asset.rating_count,
            published_at=install.asset.published_at,
            featured_at=install.asset.featured_at,
        ),
    )


@router.get(
    "/sync",
    response_model=SyncResponse,
    summary="Sync installed assets",
    description="""
Get full content for all enabled installations.

**Requires authentication.**

Used by CLI to sync installed assets locally.
Returns complete asset content for offline use.
    """,
)
async def sync_installed(
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> SyncResponse:
    """Sync installed assets with full content."""
    installs = await mp_service.sync_installed_assets(db, user.user_id)

    return SyncResponse(
        installations=[
            InstallWithContent(
                id=inst["id"],
                asset_id=inst["asset_id"],
                publisher_slug=inst["publisher_slug"],
                asset_slug=inst["asset_slug"],
                asset_type=AssetType(inst["asset_type"]),
                version=inst["version"],
                content=inst["content"],
                config=inst["config"],
                enabled=inst["enabled"],
                updated_at=inst["updated_at"],
            )
            for inst in installs
        ],
        synced_at=datetime.now(timezone.utc),
    )


# =============================================================================
# Reviews & Ratings Endpoints
# =============================================================================


@router.get(
    "/assets/{publisher_slug}/{asset_slug}/reviews",
    response_model=ReviewListResponse,
    summary="Get asset reviews",
    description="""
Get reviews for an asset.

**Public endpoint** - no authentication required.

Returns paginated reviews with rating summary.
    """,
)
async def list_reviews(
    publisher_slug: str,
    asset_slug: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ReviewListResponse:
    """List reviews for an asset."""
    asset = await mp_service.get_asset_by_publisher_slug(db, publisher_slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    result = await mp_service.get_asset_reviews(db, asset.id, page, limit)
    summary = await mp_service.get_rating_summary(db, asset.id)

    return ReviewListResponse(
        reviews=[
            ReviewResponse(
                id=review.id,
                user_id=review.user_id,
                asset_id=review.asset_id,
                rating=review.rating,
                title=review.title,
                body=review.body,
                helpful_count=review.helpful_count,
                created_at=review.created_at,
                updated_at=review.updated_at,
            )
            for review in result.items
        ],
        total=result.total,
        page=result.page,
        limit=result.limit,
        has_more=result.has_more,
        rating_summary=RatingSummary(
            average=summary.average,
            count=summary.count,
            distribution=summary.distribution,
        ),
    )


@router.post(
    "/assets/{publisher_slug}/{asset_slug}/reviews",
    response_model=ReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a review",
    description="""
Create a review for an asset.

**Requires authentication.**

Requirements:
- Must have installed the asset
- One review per user per asset
- Rating 1-5 stars required
    """,
)
async def create_review(
    publisher_slug: str,
    asset_slug: str,
    request: ReviewCreate,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    """Create a review."""
    asset = await mp_service.get_asset_by_publisher_slug(db, publisher_slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    try:
        review = await mp_service.create_review(
            db=db,
            user_id=user.user_id,
            asset=asset,
            rating=request.rating,
            title=request.title,
            body=request.body,
        )
        await db.commit()
    except mp_service.ReviewRequiresInstallError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must install this asset before reviewing",
        )
    except mp_service.DuplicateReviewError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already reviewed this asset",
        )

    return ReviewResponse(
        id=review.id,
        user_id=review.user_id,
        asset_id=review.asset_id,
        rating=review.rating,
        title=review.title,
        body=review.body,
        helpful_count=review.helpful_count,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@router.patch(
    "/assets/{publisher_slug}/{asset_slug}/reviews/me",
    response_model=ReviewResponse,
    summary="Update your review",
    description="""
Update your review for an asset.

**Requires authentication.**

Can update rating, title, and body.
    """,
)
async def update_my_review(
    publisher_slug: str,
    asset_slug: str,
    request: ReviewUpdate,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> ReviewResponse:
    """Update your review."""
    asset = await mp_service.get_asset_by_publisher_slug(db, publisher_slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    review = await mp_service.get_user_review(db, user.user_id, asset.id)
    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You have not reviewed this asset",
        )

    review = await mp_service.update_review(
        db=db,
        review=review,
        rating=request.rating,
        title=request.title,
        body=request.body,
    )
    await db.commit()

    return ReviewResponse(
        id=review.id,
        user_id=review.user_id,
        asset_id=review.asset_id,
        rating=review.rating,
        title=review.title,
        body=review.body,
        helpful_count=review.helpful_count,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@router.delete(
    "/assets/{publisher_slug}/{asset_slug}/reviews/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete your review",
    description="""
Delete your review for an asset.

**Requires authentication.**
    """,
)
async def delete_my_review(
    publisher_slug: str,
    asset_slug: str,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete your review."""
    asset = await mp_service.get_asset_by_publisher_slug(db, publisher_slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    review = await mp_service.get_user_review(db, user.user_id, asset.id)
    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You have not reviewed this asset",
        )

    await mp_service.delete_review(db, review)
    await db.commit()


# =============================================================================
# Publisher Management Endpoints
# =============================================================================


@router.get(
    "/publishers/{slug}",
    response_model=PublisherResponse,
    summary="Get publisher profile",
    description="""
Get a publisher's profile.

**Public endpoint** - no authentication required.
    """,
)
async def get_publisher(
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> PublisherResponse:
    """Get publisher by slug."""
    publisher = await mp_service.get_publisher_by_slug(db, slug)
    if not publisher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher not found",
        )

    # Count published assets
    from sqlalchemy import func, select
    from repotoire.db.models.marketplace import MarketplaceAsset

    count_result = await db.execute(
        select(func.count(MarketplaceAsset.id)).where(
            MarketplaceAsset.publisher_id == publisher.id,
            MarketplaceAsset.published_at.isnot(None),
        )
    )
    asset_count = count_result.scalar() or 0

    return PublisherResponse(
        id=publisher.id,
        type=publisher.type,
        slug=publisher.slug,
        display_name=publisher.display_name,
        description=publisher.description,
        avatar_url=publisher.avatar_url,
        website_url=publisher.website_url,
        github_url=publisher.github_url,
        verified_at=publisher.verified_at,
        created_at=publisher.created_at,
        asset_count=asset_count,
    )


@router.get(
    "/publishers/me",
    response_model=Optional[PublisherResponse],
    summary="Get my publisher profile",
    description="""
Get the current user's publisher profile.

**Requires authentication.**

Returns null if the user hasn't created a publisher profile yet.
    """,
)
async def get_my_publisher(
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> Optional[PublisherResponse]:
    """Get current user's publisher profile."""
    publisher = await mp_service.get_publisher_by_clerk_id(db, user)
    if not publisher:
        return None

    # Count published assets
    from sqlalchemy import func, select
    from repotoire.db.models.marketplace import MarketplaceAsset

    count_result = await db.execute(
        select(func.count(MarketplaceAsset.id)).where(
            MarketplaceAsset.publisher_id == publisher.id,
            MarketplaceAsset.published_at.isnot(None),
        )
    )
    asset_count = count_result.scalar() or 0

    return PublisherResponse(
        id=publisher.id,
        type=publisher.type,
        slug=publisher.slug,
        display_name=publisher.display_name,
        description=publisher.description,
        avatar_url=publisher.avatar_url,
        website_url=publisher.website_url,
        github_url=publisher.github_url,
        verified_at=publisher.verified_at,
        created_at=publisher.created_at,
        asset_count=asset_count,
    )


@router.post(
    "/publishers",
    response_model=PublisherResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create publisher profile",
    description="""
Create a publisher profile.

**Requires authentication.**

One publisher profile per user/organization.
Slug must be unique across all publishers.
    """,
)
async def create_publisher(
    request: PublisherCreate,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> PublisherResponse:
    """Create a publisher profile."""
    try:
        publisher = await mp_service.get_or_create_publisher(
            db=db,
            user=user,
            slug=request.slug,
            display_name=request.display_name,
            description=request.description,
            avatar_url=request.avatar_url,
            website_url=request.website_url,
            github_url=request.github_url,
        )
        await db.commit()
    except mp_service.SlugConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    return PublisherResponse(
        id=publisher.id,
        type=publisher.type,
        slug=publisher.slug,
        display_name=publisher.display_name,
        description=publisher.description,
        avatar_url=publisher.avatar_url,
        website_url=publisher.website_url,
        github_url=publisher.github_url,
        verified_at=publisher.verified_at,
        created_at=publisher.created_at,
        asset_count=0,
    )


@router.patch(
    "/publishers/me",
    response_model=PublisherResponse,
    summary="Update publisher profile",
    description="""
Update the current user's publisher profile.

**Requires authentication.**

Can update display name, description, and URLs.
Slug cannot be changed.
    """,
)
async def update_my_publisher(
    request: PublisherUpdate,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> PublisherResponse:
    """Update current user's publisher profile."""
    publisher = await mp_service.get_publisher_by_clerk_id(db, user)
    if not publisher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher profile not found. Create one first.",
        )

    publisher = await mp_service.update_publisher(
        db=db,
        publisher=publisher,
        display_name=request.display_name,
        description=request.description,
        avatar_url=request.avatar_url,
        website_url=request.website_url,
        github_url=request.github_url,
    )
    await db.commit()

    # Count published assets
    from sqlalchemy import func, select
    from repotoire.db.models.marketplace import MarketplaceAsset

    count_result = await db.execute(
        select(func.count(MarketplaceAsset.id)).where(
            MarketplaceAsset.publisher_id == publisher.id,
            MarketplaceAsset.published_at.isnot(None),
        )
    )
    asset_count = count_result.scalar() or 0

    return PublisherResponse(
        id=publisher.id,
        type=publisher.type,
        slug=publisher.slug,
        display_name=publisher.display_name,
        description=publisher.description,
        avatar_url=publisher.avatar_url,
        website_url=publisher.website_url,
        github_url=publisher.github_url,
        verified_at=publisher.verified_at,
        created_at=publisher.created_at,
        asset_count=asset_count,
    )


# =============================================================================
# Asset Publishing Endpoints
# =============================================================================


@router.post(
    "/assets",
    response_model=AssetDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create an asset",
    description="""
Create a new marketplace asset.

**Requires authentication.**

Requires a publisher profile. Asset is created as a draft
(not published) by default.
    """,
)
async def create_asset(
    request: AssetCreate,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> AssetDetail:
    """Create a new asset."""
    publisher = await mp_service.get_publisher_by_clerk_id(db, user)
    if not publisher:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must create a publisher profile first",
        )

    try:
        asset = await mp_service.create_asset(
            db=db,
            publisher=publisher,
            slug=request.slug,
            name=request.name,
            asset_type=request.type,
            description=request.description,
            readme=request.readme,
            icon_url=request.icon_url,
            tags=request.tags,
            pricing_type=request.pricing_type,
            price_cents=request.price_cents,
            visibility=request.visibility,
            metadata=request.metadata,
        )
        await db.commit()
    except mp_service.SlugConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except mp_service.InvalidPricingError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return AssetDetail(
        id=asset.id,
        publisher_slug=asset.publisher.slug,
        publisher_display_name=asset.publisher.display_name,
        publisher_verified=asset.publisher.is_verified,
        slug=asset.slug,
        name=asset.name,
        type=AssetType(asset.type),
        description=asset.description,
        readme=asset.readme,
        icon_url=asset.icon_url,
        tags=asset.tags,
        pricing_type=PricingType(asset.pricing_type),
        price_cents=asset.price_cents,
        visibility=AssetVisibility(asset.visibility),
        install_count=asset.install_count,
        rating_avg=asset.rating_avg,
        rating_count=asset.rating_count,
        published_at=asset.published_at,
        featured_at=asset.featured_at,
        deprecated_at=asset.deprecated_at,
        metadata=asset.asset_metadata,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        latest_version=None,
    )


@router.patch(
    "/assets/{publisher_slug}/{asset_slug}",
    response_model=AssetDetail,
    summary="Update an asset",
    description="""
Update a marketplace asset.

**Requires authentication.**

Only the publisher owner can update assets.
    """,
)
async def update_asset(
    publisher_slug: str,
    asset_slug: str,
    request: AssetUpdate,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> AssetDetail:
    """Update an asset."""
    asset = await mp_service.get_asset_by_publisher_slug(
        db, publisher_slug, asset_slug, include_versions=True
    )
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Verify ownership
    if not mp_service.verify_publisher_ownership(asset.publisher, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this asset",
        )

    try:
        asset = await mp_service.update_asset(
            db=db,
            asset=asset,
            name=request.name,
            description=request.description,
            readme=request.readme,
            icon_url=request.icon_url,
            tags=request.tags,
            pricing_type=request.pricing_type,
            price_cents=request.price_cents,
            visibility=request.visibility,
            metadata=request.metadata,
        )
        await db.commit()
    except mp_service.InvalidPricingError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    latest_version = asset.latest_version
    latest_version_str = latest_version.version if latest_version else None

    return AssetDetail(
        id=asset.id,
        publisher_slug=asset.publisher.slug,
        publisher_display_name=asset.publisher.display_name,
        publisher_verified=asset.publisher.is_verified,
        slug=asset.slug,
        name=asset.name,
        type=AssetType(asset.type),
        description=asset.description,
        readme=asset.readme,
        icon_url=asset.icon_url,
        tags=asset.tags,
        pricing_type=PricingType(asset.pricing_type),
        price_cents=asset.price_cents,
        visibility=AssetVisibility(asset.visibility),
        install_count=asset.install_count,
        rating_avg=asset.rating_avg,
        rating_count=asset.rating_count,
        published_at=asset.published_at,
        featured_at=asset.featured_at,
        deprecated_at=asset.deprecated_at,
        metadata=asset.asset_metadata,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        latest_version=latest_version_str,
    )


@router.post(
    "/assets/{publisher_slug}/{asset_slug}/versions",
    response_model=VersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new version",
    description="""
Create a new version for an asset.

**Requires authentication.**

Only the publisher owner can create versions.
Versions are immutable once published.
    """,
)
async def create_version(
    publisher_slug: str,
    asset_slug: str,
    request: VersionCreate,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> VersionResponse:
    """Create a new version."""
    asset = await mp_service.get_asset_by_publisher_slug(db, publisher_slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Verify ownership
    if not mp_service.verify_publisher_ownership(asset.publisher, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to create versions for this asset",
        )

    try:
        version = await mp_service.create_version(
            db=db,
            asset=asset,
            version=request.version,
            content=request.content,
            changelog=request.changelog,
            source_url=request.source_url,
            min_repotoire_version=request.min_repotoire_version,
            max_repotoire_version=request.max_repotoire_version,
            publish=request.publish,
        )
        await db.commit()
    except mp_service.SlugConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except mp_service.VersionRejectedError as e:
        # CRITICAL security findings - reject the version
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": str(e),
                "findings": e.findings,
            },
        )

    return VersionResponse(
        id=version.id,
        asset_id=version.asset_id,
        version=version.version,
        changelog=version.changelog,
        source_url=version.source_url,
        checksum=version.checksum,
        min_repotoire_version=version.min_repotoire_version,
        max_repotoire_version=version.max_repotoire_version,
        download_count=version.download_count,
        published_at=version.published_at,
        yanked_at=version.yanked_at,
        yank_reason=version.yank_reason,
        created_at=version.created_at,
    )


@router.get(
    "/assets/{publisher_slug}/{asset_slug}/versions",
    response_model=list[VersionResponse],
    summary="List asset versions",
    description="""
List all versions for an asset.

**Public endpoint** - no authentication required.

Returns versions sorted by creation date (newest first).
    """,
)
async def list_versions(
    publisher_slug: str,
    asset_slug: str,
    include_yanked: bool = Query(False, description="Include yanked versions"),
    db: AsyncSession = Depends(get_db),
) -> list[VersionResponse]:
    """List versions for an asset."""
    asset = await mp_service.get_asset_by_publisher_slug(db, publisher_slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    versions = await mp_service.get_asset_versions(db, asset.id, include_yanked)

    return [
        VersionResponse(
            id=version.id,
            asset_id=version.asset_id,
            version=version.version,
            changelog=version.changelog,
            source_url=version.source_url,
            checksum=version.checksum,
            min_repotoire_version=version.min_repotoire_version,
            max_repotoire_version=version.max_repotoire_version,
            download_count=version.download_count,
            published_at=version.published_at,
            yanked_at=version.yanked_at,
            yank_reason=version.yank_reason,
            created_at=version.created_at,
        )
        for version in versions
    ]


@router.get(
    "/assets/{publisher_slug}/{asset_slug}/versions/{version}",
    response_model=VersionDetailResponse,
    summary="Get version details with content",
    description="""
Get a specific version with full content.

**Public endpoint** - no authentication required.

Returns complete version including content.
    """,
)
async def get_version(
    publisher_slug: str,
    asset_slug: str,
    version: str,
    db: AsyncSession = Depends(get_db),
) -> VersionDetailResponse:
    """Get version with content."""
    asset = await mp_service.get_asset_by_publisher_slug(db, publisher_slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    ver = await mp_service.get_version_by_string(db, asset.id, version)
    if not ver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Version not found",
        )

    return VersionDetailResponse(
        id=ver.id,
        asset_id=ver.asset_id,
        version=ver.version,
        changelog=ver.changelog,
        source_url=ver.source_url,
        checksum=ver.checksum,
        min_repotoire_version=ver.min_repotoire_version,
        max_repotoire_version=ver.max_repotoire_version,
        download_count=ver.download_count,
        published_at=ver.published_at,
        yanked_at=ver.yanked_at,
        yank_reason=ver.yank_reason,
        created_at=ver.created_at,
        content=ver.content,
    )


@router.post(
    "/assets/{publisher_slug}/{asset_slug}/publish",
    response_model=AssetDetail,
    summary="Publish an asset",
    description="""
Publish an asset to the marketplace.

**Requires authentication.**

Only the publisher owner can publish assets.
Makes the asset visible in the marketplace.
    """,
)
async def publish_asset(
    publisher_slug: str,
    asset_slug: str,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> AssetDetail:
    """Publish an asset."""
    asset = await mp_service.get_asset_by_publisher_slug(
        db, publisher_slug, asset_slug, include_versions=True
    )
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Verify ownership
    if not mp_service.verify_publisher_ownership(asset.publisher, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to publish this asset",
        )

    asset = await mp_service.publish_asset(db, asset)
    await db.commit()

    latest_version = asset.latest_version
    latest_version_str = latest_version.version if latest_version else None

    return AssetDetail(
        id=asset.id,
        publisher_slug=asset.publisher.slug,
        publisher_display_name=asset.publisher.display_name,
        publisher_verified=asset.publisher.is_verified,
        slug=asset.slug,
        name=asset.name,
        type=AssetType(asset.type),
        description=asset.description,
        readme=asset.readme,
        icon_url=asset.icon_url,
        tags=asset.tags,
        pricing_type=PricingType(asset.pricing_type),
        price_cents=asset.price_cents,
        visibility=AssetVisibility(asset.visibility),
        install_count=asset.install_count,
        rating_avg=asset.rating_avg,
        rating_count=asset.rating_count,
        published_at=asset.published_at,
        featured_at=asset.featured_at,
        deprecated_at=asset.deprecated_at,
        metadata=asset.asset_metadata,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
        latest_version=latest_version_str,
    )


# =============================================================================
# Org Private Asset Endpoints
# =============================================================================


@router.get(
    "/org/assets",
    response_model=list[OrgAssetResponse],
    summary="List org-private assets",
    description="""
List private assets for the current organization.

**Requires authentication with org membership.**

Returns assets visible only to org members.
    """,
)
async def list_org_assets(
    type: Optional[AssetType] = Query(None, description="Filter by asset type"),
    enabled_only: bool = Query(False, description="Only return enabled assets"),
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> list[OrgAssetResponse]:
    """List org-private assets."""
    assets = await mp_service.get_org_assets(
        db, user.org_id, asset_type=type, enabled_only=enabled_only
    )

    return [
        OrgAssetResponse(
            id=asset.id,
            org_id=asset.org_id,
            type=AssetType(asset.type),
            slug=asset.slug,
            name=asset.name,
            description=asset.description,
            content=asset.content,
            config_schema=asset.config_schema,
            created_by_user_id=asset.created_by_user_id,
            enabled=asset.enabled,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )
        for asset in assets
    ]


@router.get(
    "/org/assets/{slug}",
    response_model=OrgAssetResponse,
    summary="Get org-private asset",
    description="""
Get a specific org-private asset.

**Requires authentication with org membership.**
    """,
)
async def get_org_asset(
    slug: str,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> OrgAssetResponse:
    """Get an org-private asset by slug."""
    asset = await mp_service.get_org_asset_by_slug(db, user.org_id, slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    return OrgAssetResponse(
        id=asset.id,
        org_id=asset.org_id,
        type=AssetType(asset.type),
        slug=asset.slug,
        name=asset.name,
        description=asset.description,
        content=asset.content,
        config_schema=asset.config_schema,
        created_by_user_id=asset.created_by_user_id,
        enabled=asset.enabled,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


@router.post(
    "/org/assets",
    response_model=OrgAssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create org-private asset",
    description="""
Create a private asset for the organization.

**Requires authentication with org membership.**
**Requires Pro or Enterprise tier.**

Org-private assets are only visible to org members.
    """,
)
async def create_org_asset(
    request: OrgAssetCreate,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> OrgAssetResponse:
    """Create an org-private asset."""
    # Check tier
    can_create = await mp_service.check_org_can_create_private_assets(db, user.org_slug)
    if not can_create:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Private org assets require Pro or Enterprise tier",
        )

    try:
        asset = await mp_service.create_org_asset(
            db=db,
            org_id=user.org_id,
            user_id=user.user_id,
            slug=request.slug,
            name=request.name,
            asset_type=request.type,
            content=request.content,
            description=request.description,
            config_schema=request.config_schema,
        )
        await db.commit()
    except mp_service.SlugConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )

    return OrgAssetResponse(
        id=asset.id,
        org_id=asset.org_id,
        type=AssetType(asset.type),
        slug=asset.slug,
        name=asset.name,
        description=asset.description,
        content=asset.content,
        config_schema=asset.config_schema,
        created_by_user_id=asset.created_by_user_id,
        enabled=asset.enabled,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


@router.patch(
    "/org/assets/{slug}",
    response_model=OrgAssetResponse,
    summary="Update org-private asset",
    description="""
Update an org-private asset.

**Requires authentication with org membership.**
    """,
)
async def update_org_asset(
    slug: str,
    request: OrgAssetUpdate,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> OrgAssetResponse:
    """Update an org-private asset."""
    asset = await mp_service.get_org_asset_by_slug(db, user.org_id, slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    asset = await mp_service.update_org_asset(
        db=db,
        asset=asset,
        name=request.name,
        description=request.description,
        content=request.content,
        config_schema=request.config_schema,
        enabled=request.enabled,
    )
    await db.commit()

    return OrgAssetResponse(
        id=asset.id,
        org_id=asset.org_id,
        type=AssetType(asset.type),
        slug=asset.slug,
        name=asset.name,
        description=asset.description,
        content=asset.content,
        config_schema=asset.config_schema,
        created_by_user_id=asset.created_by_user_id,
        enabled=asset.enabled,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


@router.delete(
    "/org/assets/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete org-private asset",
    description="""
Delete an org-private asset.

**Requires authentication with org membership.**
    """,
)
async def delete_org_asset(
    slug: str,
    user: ClerkUser = Depends(require_org),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete an org-private asset."""
    asset = await mp_service.get_org_asset_by_slug(db, user.org_id, slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    await mp_service.delete_org_asset(db, asset)
    await db.commit()


# =============================================================================
# Stripe Connect Endpoints (Publisher Payouts)
# =============================================================================


@router.post(
    "/publishers/connect",
    response_model=ConnectAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start Stripe Connect onboarding",
    description="""
Create a Stripe Connect Express account and get an onboarding URL.

**Requires authentication.**

The user must have a publisher profile. Returns a URL to complete
Stripe's hosted onboarding flow (collect bank details, identity, etc).

Platform fee: 15% on paid asset sales.
    """,
)
async def create_connect_account(
    request: ConnectAccountRequest,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> ConnectAccountResponse:
    """Create Stripe Connect account and return onboarding URL."""
    from repotoire.api.shared.services.stripe_service import StripeConnectService

    # Get publisher profile
    publisher = await mp_service.get_publisher_by_clerk_id(db, user)
    if not publisher:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must create a publisher profile first",
        )

    # Check if already connected
    if publisher.stripe_account_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Stripe account already connected. Use GET /connect/status to check status.",
        )

    # Get user email (from Clerk)
    email = user.email or f"{user.user_id}@repotoire.com"

    # Create Stripe Connect Express account
    account = StripeConnectService.create_connected_account(
        publisher_id=str(publisher.id),
        email=email,
        country=request.country,
    )

    # Save account ID to publisher
    publisher.stripe_account_id = account.id
    await db.commit()

    # Create onboarding link
    onboarding_url = StripeConnectService.create_onboarding_link(account.id)

    return ConnectAccountResponse(
        account_id=account.id,
        onboarding_url=onboarding_url,
    )


@router.get(
    "/publishers/connect/status",
    response_model=ConnectStatusResponse,
    summary="Get Stripe Connect status",
    description="""
Get the current Stripe Connect account status.

**Requires authentication.**

Returns whether the account is connected, charges/payouts enabled,
and any pending requirements.
    """,
)
async def get_connect_status(
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> ConnectStatusResponse:
    """Get Stripe Connect account status."""
    from repotoire.api.shared.services.stripe_service import StripeConnectService

    # Get publisher profile
    publisher = await mp_service.get_publisher_by_clerk_id(db, user)
    if not publisher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher profile not found",
        )

    # Not connected
    if not publisher.stripe_account_id:
        return ConnectStatusResponse(
            connected=False,
            charges_enabled=False,
            payouts_enabled=False,
            onboarding_complete=False,
            requirements=None,
            dashboard_url=None,
        )

    # Get status from Stripe
    status_info = StripeConnectService.get_account_status(publisher.stripe_account_id)

    # Update cached status in DB
    publisher.stripe_charges_enabled = status_info["charges_enabled"]
    publisher.stripe_payouts_enabled = status_info["payouts_enabled"]
    publisher.stripe_onboarding_complete = status_info["details_submitted"]
    await db.commit()

    # Get dashboard URL if connected
    dashboard_url = None
    if status_info["details_submitted"]:
        try:
            dashboard_url = StripeConnectService.create_login_link(
                publisher.stripe_account_id
            )
        except Exception:
            pass  # Login link fails if onboarding not complete

    return ConnectStatusResponse(
        connected=True,
        charges_enabled=status_info["charges_enabled"],
        payouts_enabled=status_info["payouts_enabled"],
        onboarding_complete=status_info["details_submitted"],
        requirements=status_info["requirements"],
        dashboard_url=dashboard_url,
    )


@router.get(
    "/publishers/connect/onboarding",
    summary="Get new onboarding link",
    description="""
Get a new Stripe Connect onboarding link (if existing one expired).

**Requires authentication.**

Use this if the original onboarding link has expired.
    """,
)
async def get_onboarding_link(
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get a new onboarding link for an existing account."""
    from repotoire.api.shared.services.stripe_service import StripeConnectService

    # Get publisher profile
    publisher = await mp_service.get_publisher_by_clerk_id(db, user)
    if not publisher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher profile not found",
        )

    if not publisher.stripe_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe account connected. Use POST /connect to start.",
        )

    # Create new onboarding link
    onboarding_url = StripeConnectService.create_onboarding_link(
        publisher.stripe_account_id
    )

    return {"onboarding_url": onboarding_url}


@router.get(
    "/publishers/connect/balance",
    response_model=ConnectBalanceResponse,
    summary="Get creator balance and payouts",
    description="""
Get the creator's current balance and recent payout history.

**Requires authentication.**

Shows available and pending balance, plus recent payouts.
    """,
)
async def get_connect_balance(
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> ConnectBalanceResponse:
    """Get creator's balance and payout history."""
    from repotoire.api.shared.services.stripe_service import StripeConnectService

    # Get publisher profile
    publisher = await mp_service.get_publisher_by_clerk_id(db, user)
    if not publisher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher profile not found",
        )

    if not publisher.stripe_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe account connected",
        )

    # Get balance and payouts from Stripe
    balance = StripeConnectService.get_balance(publisher.stripe_account_id)
    payouts = StripeConnectService.list_payouts(publisher.stripe_account_id, limit=10)

    return ConnectBalanceResponse(
        balance=balance,
        recent_payouts=payouts,
    )


# =============================================================================
# Purchase Endpoints (Buying Paid Assets)
# =============================================================================


@router.post(
    "/assets/{publisher_slug}/{asset_slug}/purchase",
    response_model=PurchaseResponse,
    summary="Purchase a paid asset",
    description="""
Initiate a purchase for a paid marketplace asset.

**Requires authentication.**

Returns a Stripe PaymentIntent client_secret for the frontend to
complete the payment using Stripe.js.

Platform fee: 15% (creator receives 85%).
    """,
)
async def purchase_asset(
    publisher_slug: str,
    asset_slug: str,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> PurchaseResponse:
    """Create a PaymentIntent to purchase a paid asset."""
    from repotoire.api.shared.services.stripe_service import StripeConnectService
    from repotoire.db.models.marketplace import MarketplacePurchase

    # Get the asset
    asset = await mp_service.get_asset_by_publisher_slug(
        db, publisher_slug, asset_slug, include_versions=False
    )
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Verify it's a paid asset
    if asset.pricing_type != PricingType.PAID.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This asset is not a paid asset",
        )

    if not asset.price_cents or asset.price_cents <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset has no valid price",
        )

    # Verify publisher has Stripe Connect
    publisher = asset.publisher
    if not publisher.stripe_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Publisher has not set up payment receiving",
        )

    if not publisher.stripe_charges_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Publisher's payment account is not fully set up",
        )

    # Check if already purchased
    from sqlalchemy import select

    existing_purchase = await db.execute(
        select(MarketplacePurchase).where(
            MarketplacePurchase.asset_id == asset.id,
            MarketplacePurchase.user_id == user.user_id,
            MarketplacePurchase.status == "completed",
        )
    )
    if existing_purchase.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already purchased this asset",
        )

    # Calculate fees
    amount_cents = asset.price_cents
    platform_fee_cents = int(amount_cents * StripeConnectService.PLATFORM_FEE_PERCENT)
    creator_share_cents = amount_cents - platform_fee_cents

    # Create PaymentIntent
    payment_intent = StripeConnectService.create_payment_intent(
        amount_cents=amount_cents,
        currency="usd",
        connected_account_id=publisher.stripe_account_id,
        asset_id=str(asset.id),
        buyer_user_id=user.user_id,
        publisher_id=str(publisher.id),
    )

    # Create pending purchase record
    purchase = MarketplacePurchase(
        asset_id=asset.id,
        user_id=user.user_id,
        amount_cents=amount_cents,
        platform_fee_cents=platform_fee_cents,
        creator_share_cents=creator_share_cents,
        currency="usd",
        stripe_payment_intent_id=payment_intent.id,
        status="pending",
    )
    db.add(purchase)
    await db.commit()

    return PurchaseResponse(
        client_secret=payment_intent.client_secret,
        payment_intent_id=payment_intent.id,
        amount_cents=amount_cents,
        currency="usd",
        platform_fee_cents=platform_fee_cents,
        creator_share_cents=creator_share_cents,
    )


@router.get(
    "/assets/{publisher_slug}/{asset_slug}/purchase/status",
    response_model=PurchaseStatusResponse,
    summary="Check purchase status",
    description="""
Check if the current user has purchased a specific asset.

**Requires authentication.**
    """,
)
async def get_purchase_status(
    publisher_slug: str,
    asset_slug: str,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> PurchaseStatusResponse:
    """Check if user has purchased the asset."""
    from sqlalchemy import select
    from repotoire.db.models.marketplace import MarketplacePurchase

    # Get the asset
    asset = await mp_service.get_asset_by_publisher_slug(
        db, publisher_slug, asset_slug, include_versions=False
    )
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Check for completed purchase
    result = await db.execute(
        select(MarketplacePurchase).where(
            MarketplacePurchase.asset_id == asset.id,
            MarketplacePurchase.user_id == user.user_id,
            MarketplacePurchase.status == "completed",
        )
    )
    purchase = result.scalar_one_or_none()

    if purchase:
        return PurchaseStatusResponse(
            purchased=True,
            purchase_id=purchase.id,
            purchased_at=purchase.completed_at or purchase.created_at,
        )

    return PurchaseStatusResponse(
        purchased=False,
        purchase_id=None,
        purchased_at=None,
    )


# =============================================================================
# Dependency Resolution Endpoints
# =============================================================================


@router.post(
    "/resolve",
    response_model=list[ResolvedDependencyResponse],
    summary="Resolve dependencies",
    description="""
Resolve marketplace asset dependencies using npm-style version constraints.

**Requires authentication.**

Supports constraint types:
- `^1.2.3` - caret: compatible with major version (>=1.2.3 <2.0.0)
- `~1.2.3` - tilde: patch updates only (>=1.2.3 <1.3.0)
- `>=1.0.0 <2.0.0` - explicit range
- `1.2.3` - exact version match
- `latest` - always use latest stable

Returns a flat list of resolved dependencies with download URLs.
    """,
)
async def resolve_dependencies(
    request: DependencyResolveRequest,
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> list[ResolvedDependencyResponse]:
    """Resolve dependencies and return flat list with download URLs."""
    from repotoire.marketplace import DependencyResolver
    from repotoire.cli.marketplace_client import MarketplaceAPIClient
    import os

    # Create API client (using the user's API key if available)
    api_key = os.environ.get("REPOTOIRE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server not configured for dependency resolution",
        )

    try:
        api_client = MarketplaceAPIClient(api_key=api_key)
        resolver = DependencyResolver(api_client, cache=None, lockfile=None)

        resolved = await resolver.resolve(
            dependencies=request.dependencies,
            include_dev=request.include_dev,
        )

        return [
            ResolvedDependencyResponse(
                slug=dep.slug,
                version=dep.version,
                download_url=dep.download_url,
                integrity=dep.integrity,
            )
            for dep in resolved
        ]

    except Exception as e:
        logger.exception(f"Dependency resolution failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post(
    "/outdated",
    response_model=OutdatedResponse,
    summary="Check for outdated packages",
    description="""
Check which installed packages have available updates.

**Requires authentication.**

Pass a map of installed package slugs to versions.
Returns update information for each package with an available update.
    """,
)
async def check_outdated(
    installed: dict[str, str],
    user: ClerkUser = Depends(get_current_user_or_api_key),
    db: AsyncSession = Depends(get_db),
) -> OutdatedResponse:
    """Check for outdated packages."""
    from repotoire.marketplace import AssetUpdater
    from repotoire.cli.marketplace_client import MarketplaceAPIClient
    import os

    # Create API client
    api_key = os.environ.get("REPOTOIRE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server not configured for update checking",
        )

    try:
        api_client = MarketplaceAPIClient(api_key=api_key)
        updater = AssetUpdater(api_client)

        updates = await updater.check_updates(installed)

        return OutdatedResponse(
            updates=[
                UpdateAvailableResponse(
                    slug=u.slug,
                    current=u.current,
                    latest=u.latest,
                    update_type=u.update_type.value,
                    changelog=u.changelog,
                )
                for u in updates
            ],
            total_installed=len(installed),
            total_outdated=len(updates),
        )

    except Exception as e:
        logger.exception(f"Outdated check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


# =============================================================================
# Analytics Endpoints
# =============================================================================


@router.post(
    "/analytics/events/@{publisher}/{slug}",
    response_model="TrackEventResponse",
    summary="Track an analytics event",
    description="""
Track an analytics event for an asset.

**Public endpoint** - authentication optional.

Events are used to track downloads, installs, uninstalls, and updates.
When authenticated, the event is associated with the user.

Accepts CLI headers:
- X-CLI-Version: Repotoire CLI version
- X-Platform: Operating system (darwin, linux, win32)
    """,
)
async def track_event(
    publisher: str,
    slug: str,
    request: "TrackEventRequest",
    user: Optional[ClerkUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> "TrackEventResponse":
    """Track an analytics event for an asset."""
    from repotoire.api.v1.schemas.marketplace import (
        TrackEventRequest as TrackReq,
        TrackEventResponse,
    )
    from repotoire.marketplace.analytics import AnalyticsTracker, EventData
    from repotoire.db.models.marketplace import EventType

    # Look up the asset
    asset = await mp_service.get_asset_by_slug(db, publisher, slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset @{publisher}/{slug} not found",
        )

    # Convert schema EventType to model EventType
    event_type = EventType(request.event_type.value)

    # Track the event
    tracker = AnalyticsTracker(db)
    event_data = EventData(
        asset_id=asset.id,
        event_type=event_type,
        user_id=user.user_id if user else None,
        asset_version_id=request.asset_version_id,
        cli_version=request.cli_version,
        os_platform=request.os_platform,
        source=request.source or "api",
        metadata=request.metadata,
    )

    event = await tracker.track_event(event_data)
    await db.commit()

    return TrackEventResponse(success=True, event_id=event.id)


@router.get(
    "/analytics/assets/@{publisher}/{slug}/stats",
    response_model="AssetStatsResponse",
    summary="Get asset statistics",
    description="""
Get aggregated statistics for an asset.

**Public endpoint** - no authentication required.

Returns lifetime totals and rolling window stats.
    """,
)
async def get_asset_stats(
    publisher: str,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> "AssetStatsResponse":
    """Get asset statistics."""
    from repotoire.api.v1.schemas.marketplace import AssetStatsResponse
    from repotoire.marketplace.analytics import AnalyticsTracker

    # Look up the asset
    asset = await mp_service.get_asset_by_slug(db, publisher, slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset @{publisher}/{slug} not found",
        )

    tracker = AnalyticsTracker(db)
    stats = await tracker.get_asset_stats(asset.id)

    return AssetStatsResponse(
        asset_id=stats.asset_id,
        total_downloads=stats.total_downloads,
        total_installs=stats.total_installs,
        total_uninstalls=stats.total_uninstalls,
        total_updates=stats.total_updates,
        active_installs=stats.active_installs,
        rating_avg=stats.rating_avg,
        rating_count=stats.rating_count,
        total_revenue_cents=stats.total_revenue_cents,
        total_purchases=stats.total_purchases,
        downloads_7d=stats.downloads_7d,
        downloads_30d=stats.downloads_30d,
        installs_7d=stats.installs_7d,
        installs_30d=stats.installs_30d,
    )


@router.get(
    "/analytics/assets/@{publisher}/{slug}/trends",
    response_model="AssetTrendsResponse",
    summary="Get asset trends",
    description="""
Get daily trend data for an asset.

**Public endpoint** - no authentication required.

Returns daily breakdown of events for charting.
    """,
)
async def get_asset_trends(
    publisher: str,
    slug: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days"),
    db: AsyncSession = Depends(get_db),
) -> "AssetTrendsResponse":
    """Get asset trend data."""
    from repotoire.api.v1.schemas.marketplace import AssetTrendsResponse, DailyStatsItem
    from repotoire.marketplace.analytics import AnalyticsTracker

    # Look up the asset
    asset = await mp_service.get_asset_by_slug(db, publisher, slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset @{publisher}/{slug} not found",
        )

    tracker = AnalyticsTracker(db)
    trends = await tracker.get_asset_trends(asset.id, days=days)

    return AssetTrendsResponse(
        asset_id=trends.asset_id,
        period_days=trends.period_days,
        daily_stats=[
            DailyStatsItem(
                date=datetime.combine(d.date, datetime.min.time()).replace(tzinfo=timezone.utc),
                downloads=d.downloads,
                installs=d.installs,
                uninstalls=d.uninstalls,
                updates=d.updates,
                revenue_cents=d.revenue_cents,
                unique_users=d.unique_users,
            )
            for d in trends.daily_stats
        ],
        total_downloads=trends.total_downloads,
        total_installs=trends.total_installs,
        total_uninstalls=trends.total_uninstalls,
        total_revenue_cents=trends.total_revenue_cents,
        avg_daily_downloads=trends.avg_daily_downloads,
        avg_daily_installs=trends.avg_daily_installs,
    )


@router.get(
    "/creator/stats",
    response_model="CreatorStatsResponse",
    summary="Get creator statistics",
    description="""
Get aggregated statistics for the authenticated publisher.

**Requires authentication.**

Returns stats across all publisher's assets with per-asset breakdown.
    """,
)
async def get_creator_stats(
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> "CreatorStatsResponse":
    """Get statistics for the authenticated creator."""
    from repotoire.api.v1.schemas.marketplace import (
        CreatorStatsResponse,
        AssetStatsWithName,
    )
    from repotoire.marketplace.analytics import AnalyticsTracker

    # Get the user's publisher profile
    publisher = await mp_service.get_publisher_by_user(db, user.user_id)
    if not publisher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher profile not found. Create one at /marketplace/publisher",
        )

    tracker = AnalyticsTracker(db)
    stats = await tracker.get_creator_stats(publisher.id)

    return CreatorStatsResponse(
        publisher_id=stats.publisher_id,
        total_assets=stats.total_assets,
        total_downloads=stats.total_downloads,
        total_installs=stats.total_installs,
        total_active_installs=stats.total_active_installs,
        total_revenue_cents=stats.total_revenue_cents,
        avg_rating=stats.avg_rating,
        total_reviews=stats.total_reviews,
        downloads_7d=stats.downloads_7d,
        downloads_30d=stats.downloads_30d,
        assets=[
            AssetStatsWithName(
                asset_id=a.asset_id,
                total_downloads=a.total_downloads,
                total_installs=a.total_installs,
                total_uninstalls=a.total_uninstalls,
                total_updates=a.total_updates,
                active_installs=a.active_installs,
                rating_avg=a.rating_avg,
                rating_count=a.rating_count,
                total_revenue_cents=a.total_revenue_cents,
                total_purchases=a.total_purchases,
                downloads_7d=a.downloads_7d,
                downloads_30d=a.downloads_30d,
                installs_7d=a.installs_7d,
                installs_30d=a.installs_30d,
            )
            for a in stats.assets
        ],
    )


@router.get(
    "/creator/assets/{asset_slug}/stats",
    response_model="AssetTrendsResponse",
    summary="Get detailed stats for a creator's asset",
    description="""
Get detailed statistics and trends for a specific asset.

**Requires authentication.** Only accessible by the asset's publisher.

Returns daily breakdown for charting in creator dashboard.
    """,
)
async def get_creator_asset_stats(
    asset_slug: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days"),
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> "AssetTrendsResponse":
    """Get detailed stats for a creator's asset."""
    from repotoire.api.v1.schemas.marketplace import AssetTrendsResponse, DailyStatsItem
    from repotoire.marketplace.analytics import AnalyticsTracker

    # Get the user's publisher profile
    publisher = await mp_service.get_publisher_by_user(db, user.user_id)
    if not publisher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publisher profile not found",
        )

    # Look up the asset
    asset = await mp_service.get_asset_by_slug(db, publisher.slug, asset_slug)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset {asset_slug} not found",
        )

    # Verify ownership
    if asset.publisher_id != publisher.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not own this asset",
        )

    tracker = AnalyticsTracker(db)
    trends = await tracker.get_asset_trends(asset.id, days=days)

    return AssetTrendsResponse(
        asset_id=trends.asset_id,
        period_days=trends.period_days,
        daily_stats=[
            DailyStatsItem(
                date=datetime.combine(d.date, datetime.min.time()).replace(tzinfo=timezone.utc),
                downloads=d.downloads,
                installs=d.installs,
                uninstalls=d.uninstalls,
                updates=d.updates,
                revenue_cents=d.revenue_cents,
                unique_users=d.unique_users,
            )
            for d in trends.daily_stats
        ],
        total_downloads=trends.total_downloads,
        total_installs=trends.total_installs,
        total_uninstalls=trends.total_uninstalls,
        total_revenue_cents=trends.total_revenue_cents,
        avg_daily_downloads=trends.avg_daily_downloads,
        avg_daily_installs=trends.avg_daily_installs,
    )


@router.get(
    "/admin/analytics/overview",
    response_model="PlatformStatsResponse",
    summary="Get platform-wide analytics (admin only)",
    description="""
Get platform-wide analytics overview.

**Requires admin authentication.**

Returns aggregate metrics and top assets lists.
    """,
)
async def get_admin_analytics_overview(
    limit: int = Query(default=10, ge=1, le=100, description="Number of top assets"),
    user: ClerkUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> "PlatformStatsResponse":
    """Get platform-wide analytics overview (admin only)."""
    from repotoire.api.v1.schemas.marketplace import PlatformStatsResponse, TopAssetItem
    from repotoire.marketplace.analytics import AnalyticsTracker

    # TODO: Add proper admin role check
    # For now, just require authentication
    if not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    tracker = AnalyticsTracker(db)
    stats = await tracker.get_platform_stats(limit=limit)

    return PlatformStatsResponse(
        total_assets=stats.total_assets,
        total_publishers=stats.total_publishers,
        total_downloads=stats.total_downloads,
        total_installs=stats.total_installs,
        total_active_installs=stats.total_active_installs,
        total_revenue_cents=stats.total_revenue_cents,
        downloads_7d=stats.downloads_7d,
        downloads_30d=stats.downloads_30d,
        installs_7d=stats.installs_7d,
        installs_30d=stats.installs_30d,
        top_by_downloads=[
            TopAssetItem(
                id=a["id"],
                name=a["name"],
                slug=a["slug"],
                publisher_slug=a["publisher_slug"],
                value=a["total_downloads"],
            )
            for a in stats.top_by_downloads
        ],
        top_by_installs=[
            TopAssetItem(
                id=a["id"],
                name=a["name"],
                slug=a["slug"],
                publisher_slug=a["publisher_slug"],
                value=a["active_installs"],
            )
            for a in stats.top_by_installs
        ],
        top_by_revenue=[
            TopAssetItem(
                id=a["id"],
                name=a["name"],
                slug=a["slug"],
                publisher_slug=a["publisher_slug"],
                value=a["total_revenue_cents"],
            )
            for a in stats.top_by_revenue
        ],
    )

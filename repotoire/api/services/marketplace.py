"""Marketplace service layer.

This module contains business logic for the marketplace API:
- Publisher management
- Asset CRUD and search
- Version management
- Installation handling
- Review management with rating aggregation
- Org-private asset management
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.api.shared.auth import ClerkUser
from repotoire.db.models import Organization, PlanTier
from repotoire.db.models.marketplace import (
    AssetSecurityReview,
    AssetType,
    AssetVisibility,
    MarketplaceAsset,
    MarketplaceAssetVersion,
    MarketplaceInstall,
    MarketplacePublisher,
    MarketplaceReview,
    OrgPrivateAsset,
    PricingType,
    PublisherType,
)
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class MarketplaceError(Exception):
    """Base exception for marketplace errors."""

    pass


class PublisherNotFoundError(MarketplaceError):
    """Publisher not found."""

    pass


class AssetNotFoundError(MarketplaceError):
    """Asset not found."""

    pass


class VersionNotFoundError(MarketplaceError):
    """Version not found."""

    pass


class SlugConflictError(MarketplaceError):
    """Slug already exists."""

    pass


class NotAuthorizedError(MarketplaceError):
    """User not authorized for this operation."""

    pass


class InstallLimitExceededError(MarketplaceError):
    """Installation limit exceeded for tier."""

    pass


class ReviewRequiresInstallError(MarketplaceError):
    """User must install asset before reviewing."""

    pass


class DuplicateReviewError(MarketplaceError):
    """User already reviewed this asset."""

    pass


class InvalidPricingError(MarketplaceError):
    """Invalid pricing configuration."""

    pass


# =============================================================================
# Result Types
# =============================================================================


@dataclass
class PaginatedResult:
    """Paginated query result."""

    items: list
    total: int
    page: int
    limit: int
    has_more: bool


@dataclass
class RatingSummary:
    """Summary of ratings for an asset."""

    average: Optional[Decimal]
    count: int
    distribution: dict[int, int]  # rating -> count


# =============================================================================
# Publisher Service
# =============================================================================


async def get_publisher_by_slug(
    db: AsyncSession,
    slug: str,
) -> Optional[MarketplacePublisher]:
    """Get a publisher by slug."""
    result = await db.execute(
        select(MarketplacePublisher).where(MarketplacePublisher.slug == slug)
    )
    return result.scalar_one_or_none()


async def get_publisher_by_clerk_id(
    db: AsyncSession,
    user: ClerkUser,
) -> Optional[MarketplacePublisher]:
    """Get a publisher by Clerk user or org ID."""
    if user.org_id:
        result = await db.execute(
            select(MarketplacePublisher).where(
                MarketplacePublisher.clerk_org_id == user.org_id
            )
        )
    else:
        result = await db.execute(
            select(MarketplacePublisher).where(
                MarketplacePublisher.clerk_user_id == user.user_id
            )
        )
    return result.scalar_one_or_none()


async def get_or_create_publisher(
    db: AsyncSession,
    user: ClerkUser,
    slug: str,
    display_name: str,
    description: Optional[str] = None,
    avatar_url: Optional[str] = None,
    website_url: Optional[str] = None,
    github_url: Optional[str] = None,
) -> MarketplacePublisher:
    """Get existing publisher or create a new one."""
    # Check if publisher already exists
    existing = await get_publisher_by_clerk_id(db, user)
    if existing:
        return existing

    # Check slug uniqueness
    if await get_publisher_by_slug(db, slug):
        raise SlugConflictError(f"Publisher slug '{slug}' is already taken")

    # Create new publisher
    publisher_type = PublisherType.ORGANIZATION if user.org_id else PublisherType.USER
    publisher = MarketplacePublisher(
        type=publisher_type.value,
        clerk_user_id=user.user_id if not user.org_id else None,
        clerk_org_id=user.org_id if user.org_id else None,
        slug=slug,
        display_name=display_name,
        description=description,
        avatar_url=avatar_url,
        website_url=website_url,
        github_url=github_url,
    )
    db.add(publisher)
    await db.flush()
    await db.refresh(publisher)
    return publisher


async def update_publisher(
    db: AsyncSession,
    publisher: MarketplacePublisher,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    avatar_url: Optional[str] = None,
    website_url: Optional[str] = None,
    github_url: Optional[str] = None,
) -> MarketplacePublisher:
    """Update a publisher profile."""
    if display_name is not None:
        publisher.display_name = display_name
    if description is not None:
        publisher.description = description
    if avatar_url is not None:
        publisher.avatar_url = avatar_url
    if website_url is not None:
        publisher.website_url = website_url
    if github_url is not None:
        publisher.github_url = github_url

    await db.flush()
    await db.refresh(publisher)
    return publisher


def verify_publisher_ownership(publisher: MarketplacePublisher, user: ClerkUser) -> bool:
    """Check if user owns the publisher."""
    if publisher.type == PublisherType.ORGANIZATION.value:
        return publisher.clerk_org_id == user.org_id
    return publisher.clerk_user_id == user.user_id


# =============================================================================
# Asset Service
# =============================================================================


async def get_asset_by_id(
    db: AsyncSession,
    asset_id: UUID,
    include_versions: bool = False,
) -> Optional[MarketplaceAsset]:
    """Get an asset by ID."""
    query = select(MarketplaceAsset).where(MarketplaceAsset.id == asset_id)
    if include_versions:
        query = query.options(selectinload(MarketplaceAsset.versions))
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_asset_by_publisher_slug(
    db: AsyncSession,
    publisher_slug: str,
    asset_slug: str,
    include_versions: bool = False,
) -> Optional[MarketplaceAsset]:
    """Get an asset by publisher and asset slugs."""
    query = (
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
    if include_versions:
        query = query.options(selectinload(MarketplaceAsset.versions))
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def search_assets(
    db: AsyncSession,
    query: Optional[str] = None,
    asset_type: Optional[AssetType] = None,
    tags: Optional[list[str]] = None,
    pricing_type: Optional[PricingType] = None,
    publisher_slug: Optional[str] = None,
    featured_only: bool = False,
    sort_by: str = "popular",
    page: int = 1,
    limit: int = 20,
    visibility: AssetVisibility = AssetVisibility.PUBLIC,
) -> PaginatedResult:
    """Search and filter assets."""
    # Base query for published public assets
    base_query = (
        select(MarketplaceAsset)
        .join(MarketplacePublisher)
        .where(
            and_(
                MarketplaceAsset.visibility == visibility.value,
                MarketplaceAsset.published_at.isnot(None),
                MarketplaceAsset.deprecated_at.is_(None),
            )
        )
        .options(selectinload(MarketplaceAsset.publisher))
    )

    # Apply filters
    if query:
        # Use PostgreSQL full-text search on search_vector column
        base_query = base_query.where(
            text("marketplace_assets.search_vector @@ plainto_tsquery('english', :query)")
        ).params(query=query)

    if asset_type:
        base_query = base_query.where(MarketplaceAsset.type == asset_type.value)

    if tags:
        # GIN array contains any of the tags
        base_query = base_query.where(MarketplaceAsset.tags.overlap(tags))

    if pricing_type:
        base_query = base_query.where(MarketplaceAsset.pricing_type == pricing_type.value)

    if publisher_slug:
        base_query = base_query.where(MarketplacePublisher.slug == publisher_slug)

    if featured_only:
        base_query = base_query.where(MarketplaceAsset.featured_at.isnot(None))

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply sorting
    if sort_by == "popular":
        base_query = base_query.order_by(desc(MarketplaceAsset.install_count))
    elif sort_by == "recent":
        base_query = base_query.order_by(desc(MarketplaceAsset.published_at))
    elif sort_by == "rating":
        base_query = base_query.order_by(
            desc(MarketplaceAsset.rating_avg).nullslast(),
            desc(MarketplaceAsset.rating_count),
        )
    elif sort_by == "name":
        base_query = base_query.order_by(MarketplaceAsset.name)
    else:
        base_query = base_query.order_by(desc(MarketplaceAsset.install_count))

    # Apply pagination
    offset = (page - 1) * limit
    base_query = base_query.offset(offset).limit(limit)

    result = await db.execute(base_query)
    items = list(result.scalars().all())

    return PaginatedResult(
        items=items,
        total=total,
        page=page,
        limit=limit,
        has_more=(offset + len(items)) < total,
    )


async def create_asset(
    db: AsyncSession,
    publisher: MarketplacePublisher,
    slug: str,
    name: str,
    asset_type: AssetType,
    description: Optional[str] = None,
    readme: Optional[str] = None,
    icon_url: Optional[str] = None,
    tags: Optional[list[str]] = None,
    pricing_type: PricingType = PricingType.FREE,
    price_cents: Optional[int] = None,
    visibility: AssetVisibility = AssetVisibility.PUBLIC,
    metadata: Optional[dict] = None,
) -> MarketplaceAsset:
    """Create a new asset."""
    # Validate pricing
    if pricing_type == PricingType.PAID and (not price_cents or price_cents <= 0):
        raise InvalidPricingError("Paid assets require a positive price")

    # Check slug uniqueness within publisher
    existing = await db.execute(
        select(MarketplaceAsset).where(
            and_(
                MarketplaceAsset.publisher_id == publisher.id,
                MarketplaceAsset.slug == slug,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise SlugConflictError(f"Asset slug '{slug}' already exists for this publisher")

    asset = MarketplaceAsset(
        publisher_id=publisher.id,
        type=asset_type.value,
        slug=slug,
        name=name,
        description=description,
        readme=readme,
        icon_url=icon_url,
        tags=tags,
        pricing_type=pricing_type.value,
        price_cents=price_cents,
        visibility=visibility.value,
        asset_metadata=metadata,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset, ["publisher"])
    return asset


async def update_asset(
    db: AsyncSession,
    asset: MarketplaceAsset,
    name: Optional[str] = None,
    description: Optional[str] = None,
    readme: Optional[str] = None,
    icon_url: Optional[str] = None,
    tags: Optional[list[str]] = None,
    pricing_type: Optional[PricingType] = None,
    price_cents: Optional[int] = None,
    visibility: Optional[AssetVisibility] = None,
    metadata: Optional[dict] = None,
) -> MarketplaceAsset:
    """Update an asset."""
    if name is not None:
        asset.name = name
    if description is not None:
        asset.description = description
    if readme is not None:
        asset.readme = readme
    if icon_url is not None:
        asset.icon_url = icon_url
    if tags is not None:
        asset.tags = tags
    if pricing_type is not None:
        # Validate pricing
        effective_price = price_cents if price_cents is not None else asset.price_cents
        if pricing_type == PricingType.PAID and (not effective_price or effective_price <= 0):
            raise InvalidPricingError("Paid assets require a positive price")
        asset.pricing_type = pricing_type.value
    if price_cents is not None:
        asset.price_cents = price_cents
    if visibility is not None:
        asset.visibility = visibility.value
    if metadata is not None:
        asset.asset_metadata = metadata

    await db.flush()
    await db.refresh(asset)
    return asset


async def publish_asset(
    db: AsyncSession,
    asset: MarketplaceAsset,
) -> MarketplaceAsset:
    """Publish an asset (make it live)."""
    if asset.published_at is None:
        asset.published_at = datetime.now(timezone.utc)
        await db.flush()
        await db.refresh(asset)
    return asset


async def deprecate_asset(
    db: AsyncSession,
    asset: MarketplaceAsset,
) -> MarketplaceAsset:
    """Mark an asset as deprecated."""
    if asset.deprecated_at is None:
        asset.deprecated_at = datetime.now(timezone.utc)
        await db.flush()
        await db.refresh(asset)
    return asset


async def get_featured_assets(
    db: AsyncSession,
    limit: int = 6,
) -> list[MarketplaceAsset]:
    """Get featured marketplace assets."""
    query = (
        select(MarketplaceAsset)
        .join(MarketplacePublisher)
        .where(
            and_(
                MarketplaceAsset.visibility == AssetVisibility.PUBLIC.value,
                MarketplaceAsset.published_at.isnot(None),
                MarketplaceAsset.deprecated_at.is_(None),
                MarketplaceAsset.featured_at.isnot(None),
            )
        )
        .options(selectinload(MarketplaceAsset.publisher))
        .order_by(desc(MarketplaceAsset.featured_at))
        .limit(limit)
    )

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_tags_with_counts(
    db: AsyncSession,
    limit: int = 50,
) -> list[dict]:
    """Get all tags with their asset counts, ordered by count descending.

    Uses PostgreSQL unnest to explode the tags array and aggregate counts.
    Only includes tags from published, public, non-deprecated assets.
    """
    # Use unnest to explode the tags array and count
    query = text("""
        SELECT tag, COUNT(*) as count
        FROM marketplace_assets,
             LATERAL unnest(tags) as tag
        WHERE visibility = 'public'
          AND published_at IS NOT NULL
          AND deprecated_at IS NULL
          AND tags IS NOT NULL
        GROUP BY tag
        ORDER BY count DESC, tag ASC
        LIMIT :limit
    """)

    result = await db.execute(query, {"limit": limit})
    return [{"tag": row.tag, "count": row.count} for row in result]


async def get_categories_with_counts(db: AsyncSession) -> list[dict]:
    """Get asset type categories with counts."""
    result = await db.execute(
        select(
            MarketplaceAsset.type,
            func.count(MarketplaceAsset.id).label("count"),
        )
        .where(
            and_(
                MarketplaceAsset.visibility == AssetVisibility.PUBLIC.value,
                MarketplaceAsset.published_at.isnot(None),
                MarketplaceAsset.deprecated_at.is_(None),
            )
        )
        .group_by(MarketplaceAsset.type)
    )

    counts = {row.type: row.count for row in result}

    # Category metadata
    category_info = {
        AssetType.SKILL: {
            "display_name": "Skills",
            "description": "MCP skills that extend Claude's capabilities",
            "icon": "zap",
        },
        AssetType.COMMAND: {
            "display_name": "Commands",
            "description": "Slash commands for common workflows",
            "icon": "terminal",
        },
        AssetType.STYLE: {
            "display_name": "Styles",
            "description": "Personas and communication styles for Claude",
            "icon": "palette",
        },
        AssetType.HOOK: {
            "display_name": "Hooks",
            "description": "Lifecycle hooks for automation",
            "icon": "link",
        },
        AssetType.PROMPT: {
            "display_name": "Prompts",
            "description": "Reusable prompt templates",
            "icon": "message-square",
        },
    }

    return [
        {
            "type": asset_type,
            "display_name": info["display_name"],
            "description": info["description"],
            "icon": info["icon"],
            "asset_count": counts.get(asset_type.value, 0),
        }
        for asset_type, info in category_info.items()
    ]


# =============================================================================
# Version Service
# =============================================================================


def compute_content_checksum(content: dict) -> str:
    """Compute SHA256 checksum of content."""
    content_str = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content_str.encode()).hexdigest()


async def get_version_by_id(
    db: AsyncSession,
    version_id: UUID,
) -> Optional[MarketplaceAssetVersion]:
    """Get a version by ID."""
    result = await db.execute(
        select(MarketplaceAssetVersion)
        .where(MarketplaceAssetVersion.id == version_id)
        .options(selectinload(MarketplaceAssetVersion.asset))
    )
    return result.scalar_one_or_none()


async def get_version_by_string(
    db: AsyncSession,
    asset_id: UUID,
    version: str,
) -> Optional[MarketplaceAssetVersion]:
    """Get a version by asset ID and version string."""
    result = await db.execute(
        select(MarketplaceAssetVersion).where(
            and_(
                MarketplaceAssetVersion.asset_id == asset_id,
                MarketplaceAssetVersion.version == version,
            )
        )
    )
    return result.scalar_one_or_none()


async def get_latest_version(
    db: AsyncSession,
    asset_id: UUID,
    published_only: bool = True,
) -> Optional[MarketplaceAssetVersion]:
    """Get the latest version for an asset."""
    query = (
        select(MarketplaceAssetVersion)
        .where(
            and_(
                MarketplaceAssetVersion.asset_id == asset_id,
                MarketplaceAssetVersion.yanked_at.is_(None),
            )
        )
        .order_by(desc(MarketplaceAssetVersion.created_at))
    )

    if published_only:
        query = query.where(MarketplaceAssetVersion.published_at.isnot(None))

    query = query.limit(1)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def scan_version_content(
    db: AsyncSession,
    version: MarketplaceAssetVersion,
    publisher: MarketplacePublisher,
) -> AssetSecurityReview:
    """Scan version content and create security review record.

    This function:
    1. Scans the content for dangerous patterns
    2. Creates an AssetSecurityReview record
    3. Determines review status based on findings and publisher trust

    Args:
        db: Database session
        version: The version to scan
        publisher: The publisher (for trust level checking)

    Returns:
        AssetSecurityReview record with scan results
    """
    from repotoire.marketplace.scanner import AssetScanner, SeverityLevel

    scanner = AssetScanner()
    content = version.content or {}

    # Scan the content (which is a dict, we need to scan text fields)
    findings = []
    for key, value in content.items():
        if isinstance(value, str):
            findings.extend(scanner._scan_content(value, f"content.{key}"))
        elif isinstance(value, dict):
            # Recursively scan nested dicts
            for nested_key, nested_value in value.items():
                if isinstance(nested_value, str):
                    findings.extend(
                        scanner._scan_content(nested_value, f"content.{key}.{nested_key}")
                    )

    # Get the verdict
    verdict, verdict_message = scanner.get_verdict(findings)

    # Determine review status based on verdict and publisher trust
    is_trusted = publisher.is_verified
    has_critical = any(f.severity == SeverityLevel.CRITICAL for f in findings)
    has_high = any(f.severity == SeverityLevel.HIGH for f in findings)

    if has_critical:
        # CRITICAL findings always require review (auto-rejected)
        review_status = "rejected"
    elif has_high:
        # HIGH findings require manual review
        review_status = "pending"
    elif findings and not is_trusted:
        # Non-trusted publishers with any findings need review
        review_status = "pending"
    elif findings and is_trusted:
        # Trusted publishers with low/medium can be auto-approved with warnings
        review_status = "approved"
    else:
        # No findings - auto-approve
        review_status = "approved"

    # Create the security review record
    review = AssetSecurityReview(
        asset_version_id=version.id,
        status=review_status,
        scan_findings=[f.to_dict() for f in findings] if findings else None,
        scan_verdict=verdict,
        scanned_at=datetime.now(timezone.utc),
        reviewer_notes=verdict_message if verdict != "approved" else None,
    )

    # If auto-approved, set reviewed_at
    if review_status == "approved":
        review.reviewed_at = datetime.now(timezone.utc)

    db.add(review)
    await db.flush()
    await db.refresh(review)

    logger.info(
        f"Security scan completed for version {version.id}",
        extra={
            "version_id": str(version.id),
            "finding_count": len(findings),
            "verdict": verdict,
            "review_status": review_status,
        },
    )

    return review


class VersionRejectedError(MarketplaceError):
    """Version was rejected due to security findings."""

    def __init__(self, message: str, findings: list):
        super().__init__(message)
        self.findings = findings


async def create_version(
    db: AsyncSession,
    asset: MarketplaceAsset,
    version: str,
    content: dict,
    changelog: Optional[str] = None,
    source_url: Optional[str] = None,
    min_repotoire_version: Optional[str] = None,
    max_repotoire_version: Optional[str] = None,
    publish: bool = False,
    skip_scan: bool = False,
) -> MarketplaceAssetVersion:
    """Create a new version for an asset.

    Args:
        db: Database session
        asset: The asset to add version to
        version: Version string (semver)
        content: Version content (code, config, etc.)
        changelog: Version changelog text
        source_url: Link to source code
        min_repotoire_version: Minimum compatible Repotoire version
        max_repotoire_version: Maximum compatible Repotoire version
        publish: Whether to publish immediately (subject to security review)
        skip_scan: Skip security scanning (internal use only)

    Returns:
        The created version

    Raises:
        SlugConflictError: If version already exists
        VersionRejectedError: If content contains CRITICAL security issues
    """
    # Check version doesn't exist
    existing = await get_version_by_string(db, asset.id, version)
    if existing:
        raise SlugConflictError(f"Version '{version}' already exists for this asset")

    checksum = compute_content_checksum(content)

    # Create version (don't publish yet if we need to scan)
    version_obj = MarketplaceAssetVersion(
        asset_id=asset.id,
        version=version,
        changelog=changelog,
        content=content,
        source_url=source_url,
        checksum=checksum,
        min_repotoire_version=min_repotoire_version,
        max_repotoire_version=max_repotoire_version,
        published_at=None,  # Set after scan if approved
    )
    db.add(version_obj)
    await db.flush()
    await db.refresh(version_obj, ["asset"])

    # Run security scan unless skipped
    if not skip_scan:
        # Get publisher for trust check
        publisher = asset.publisher
        if not publisher:
            # Load publisher if not already loaded
            await db.refresh(asset, ["publisher"])
            publisher = asset.publisher

        review = await scan_version_content(db, version_obj, publisher)

        # Handle based on review status
        if review.status == "rejected":
            # CRITICAL findings - raise error
            raise VersionRejectedError(
                "Version rejected due to critical security findings",
                review.scan_findings or [],
            )
        elif review.status == "pending":
            # HIGH findings - version created but not published
            logger.warning(
                f"Version {version_obj.id} pending security review",
                extra={"version_id": str(version_obj.id)},
            )
            # Don't publish even if requested
            publish = False
        # "approved" - can proceed with publish if requested

    # Publish if requested and allowed
    if publish:
        version_obj.published_at = datetime.now(timezone.utc)
        await db.flush()

    await db.refresh(version_obj)
    return version_obj


async def publish_version(
    db: AsyncSession,
    version: MarketplaceAssetVersion,
) -> MarketplaceAssetVersion:
    """Publish a version."""
    if version.published_at is None:
        version.published_at = datetime.now(timezone.utc)
        await db.flush()
        await db.refresh(version)
    return version


async def yank_version(
    db: AsyncSession,
    version: MarketplaceAssetVersion,
    reason: str,
) -> MarketplaceAssetVersion:
    """Yank a version (mark as unsafe/broken)."""
    if version.yanked_at is None:
        version.yanked_at = datetime.now(timezone.utc)
        version.yank_reason = reason
        await db.flush()
        await db.refresh(version)
    return version


async def get_asset_versions(
    db: AsyncSession,
    asset_id: UUID,
    include_yanked: bool = False,
) -> list[MarketplaceAssetVersion]:
    """Get all versions for an asset."""
    query = (
        select(MarketplaceAssetVersion)
        .where(MarketplaceAssetVersion.asset_id == asset_id)
        .order_by(desc(MarketplaceAssetVersion.created_at))
    )

    if not include_yanked:
        query = query.where(MarketplaceAssetVersion.yanked_at.is_(None))

    result = await db.execute(query)
    return list(result.scalars().all())


# =============================================================================
# Installation Service
# =============================================================================


async def get_user_installs(
    db: AsyncSession,
    user_id: str,
    enabled_only: bool = False,
) -> list[MarketplaceInstall]:
    """Get all installations for a user."""
    query = (
        select(MarketplaceInstall)
        .where(MarketplaceInstall.user_id == user_id)
        .options(
            selectinload(MarketplaceInstall.asset).selectinload(MarketplaceAsset.publisher),
            selectinload(MarketplaceInstall.version),
        )
        .order_by(desc(MarketplaceInstall.created_at))
    )

    if enabled_only:
        query = query.where(MarketplaceInstall.enabled == True)  # noqa: E712

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_install(
    db: AsyncSession,
    user_id: str,
    asset_id: UUID,
) -> Optional[MarketplaceInstall]:
    """Get a specific installation."""
    result = await db.execute(
        select(MarketplaceInstall)
        .where(
            and_(
                MarketplaceInstall.user_id == user_id,
                MarketplaceInstall.asset_id == asset_id,
            )
        )
        .options(
            selectinload(MarketplaceInstall.asset).selectinload(MarketplaceAsset.publisher),
            selectinload(MarketplaceInstall.version),
        )
    )
    return result.scalar_one_or_none()


async def install_asset(
    db: AsyncSession,
    user_id: str,
    asset: MarketplaceAsset,
    version: Optional[MarketplaceAssetVersion] = None,
    config: Optional[dict] = None,
    auto_update: bool = True,
) -> MarketplaceInstall:
    """Install an asset for a user."""
    # Check if already installed
    existing = await get_install(db, user_id, asset.id)
    if existing:
        # Update existing installation
        if version:
            existing.version_id = version.id
        if config is not None:
            existing.config = config
        existing.auto_update = auto_update
        existing.enabled = True
        await db.flush()
        await db.refresh(existing, ["asset", "version"])
        return existing

    # Get latest version if not specified
    if not version:
        version = await get_latest_version(db, asset.id)

    install = MarketplaceInstall(
        user_id=user_id,
        asset_id=asset.id,
        version_id=version.id if version else None,
        config=config,
        auto_update=auto_update,
    )
    db.add(install)

    # Update install count
    asset.install_count += 1

    await db.flush()
    await db.refresh(install, ["asset", "version"])
    return install


async def uninstall_asset(
    db: AsyncSession,
    user_id: str,
    asset: MarketplaceAsset,
) -> bool:
    """Uninstall an asset for a user."""
    install = await get_install(db, user_id, asset.id)
    if not install:
        return False

    await db.delete(install)

    # Update install count
    if asset.install_count > 0:
        asset.install_count -= 1

    await db.flush()
    return True


async def update_install(
    db: AsyncSession,
    install: MarketplaceInstall,
    version: Optional[MarketplaceAssetVersion] = None,
    config: Optional[dict] = None,
    enabled: Optional[bool] = None,
    auto_update: Optional[bool] = None,
) -> MarketplaceInstall:
    """Update an installation."""
    if version is not None:
        install.version_id = version.id
    if config is not None:
        install.config = config
    if enabled is not None:
        install.enabled = enabled
    if auto_update is not None:
        install.auto_update = auto_update

    await db.flush()
    await db.refresh(install, ["asset", "version"])
    return install


async def sync_installed_assets(
    db: AsyncSession,
    user_id: str,
) -> list[dict]:
    """Get full content for all enabled installations (for CLI sync)."""
    installs = await get_user_installs(db, user_id, enabled_only=True)

    result = []
    for install in installs:
        # Get version content
        version = install.version
        if not version:
            version = await get_latest_version(db, install.asset_id)
        if not version:
            continue

        result.append({
            "id": install.id,
            "asset_id": install.asset_id,
            "publisher_slug": install.asset.publisher.slug,
            "asset_slug": install.asset.slug,
            "asset_type": install.asset.type,
            "version": version.version,
            "content": version.content,
            "config": install.config,
            "enabled": install.enabled,
            "updated_at": install.updated_at,
        })

    return result


# =============================================================================
# Review Service
# =============================================================================


async def get_asset_reviews(
    db: AsyncSession,
    asset_id: UUID,
    page: int = 1,
    limit: int = 20,
) -> PaginatedResult:
    """Get reviews for an asset."""
    base_query = (
        select(MarketplaceReview)
        .where(
            and_(
                MarketplaceReview.asset_id == asset_id,
                MarketplaceReview.hidden_at.is_(None),
            )
        )
        .order_by(desc(MarketplaceReview.created_at))
    )

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * limit
    base_query = base_query.offset(offset).limit(limit)

    result = await db.execute(base_query)
    items = list(result.scalars().all())

    return PaginatedResult(
        items=items,
        total=total,
        page=page,
        limit=limit,
        has_more=(offset + len(items)) < total,
    )


async def get_rating_summary(
    db: AsyncSession,
    asset_id: UUID,
) -> RatingSummary:
    """Get rating summary for an asset."""
    # Get distribution
    dist_result = await db.execute(
        select(
            MarketplaceReview.rating,
            func.count(MarketplaceReview.id).label("count"),
        )
        .where(
            and_(
                MarketplaceReview.asset_id == asset_id,
                MarketplaceReview.hidden_at.is_(None),
            )
        )
        .group_by(MarketplaceReview.rating)
    )

    distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    total_count = 0
    total_sum = 0

    for row in dist_result:
        distribution[row.rating] = row.count
        total_count += row.count
        total_sum += row.rating * row.count

    average = Decimal(total_sum) / Decimal(total_count) if total_count > 0 else None
    if average:
        average = round(average, 2)

    return RatingSummary(
        average=average,
        count=total_count,
        distribution=distribution,
    )


async def get_user_review(
    db: AsyncSession,
    user_id: str,
    asset_id: UUID,
) -> Optional[MarketplaceReview]:
    """Get a user's review for an asset."""
    result = await db.execute(
        select(MarketplaceReview).where(
            and_(
                MarketplaceReview.user_id == user_id,
                MarketplaceReview.asset_id == asset_id,
            )
        )
    )
    return result.scalar_one_or_none()


async def create_review(
    db: AsyncSession,
    user_id: str,
    asset: MarketplaceAsset,
    rating: int,
    title: Optional[str] = None,
    body: Optional[str] = None,
) -> MarketplaceReview:
    """Create a review for an asset."""
    # Check if user has installed the asset
    install = await get_install(db, user_id, asset.id)
    if not install:
        raise ReviewRequiresInstallError("You must install this asset before reviewing")

    # Check for existing review
    existing = await get_user_review(db, user_id, asset.id)
    if existing:
        raise DuplicateReviewError("You have already reviewed this asset")

    review = MarketplaceReview(
        user_id=user_id,
        asset_id=asset.id,
        rating=rating,
        title=title,
        body=body,
    )
    db.add(review)

    # Update aggregate ratings
    await _update_asset_ratings(db, asset)

    await db.flush()
    await db.refresh(review)
    return review


async def update_review(
    db: AsyncSession,
    review: MarketplaceReview,
    rating: Optional[int] = None,
    title: Optional[str] = None,
    body: Optional[str] = None,
) -> MarketplaceReview:
    """Update a review."""
    if rating is not None:
        review.rating = rating
    if title is not None:
        review.title = title
    if body is not None:
        review.body = body

    # Update aggregate ratings
    asset = await get_asset_by_id(db, review.asset_id)
    if asset:
        await _update_asset_ratings(db, asset)

    await db.flush()
    await db.refresh(review)
    return review


async def delete_review(
    db: AsyncSession,
    review: MarketplaceReview,
) -> None:
    """Delete a review."""
    asset_id = review.asset_id
    await db.delete(review)

    # Update aggregate ratings
    asset = await get_asset_by_id(db, asset_id)
    if asset:
        await _update_asset_ratings(db, asset)

    await db.flush()


async def _update_asset_ratings(
    db: AsyncSession,
    asset: MarketplaceAsset,
) -> None:
    """Update denormalized rating stats on asset."""
    summary = await get_rating_summary(db, asset.id)
    asset.rating_avg = summary.average
    asset.rating_count = summary.count


# =============================================================================
# Org Private Asset Service
# =============================================================================


async def get_org_assets(
    db: AsyncSession,
    org_id: str,
    asset_type: Optional[AssetType] = None,
    enabled_only: bool = False,
) -> list[OrgPrivateAsset]:
    """Get all org-private assets for an organization."""
    query = (
        select(OrgPrivateAsset)
        .where(OrgPrivateAsset.org_id == org_id)
        .order_by(desc(OrgPrivateAsset.created_at))
    )

    if asset_type:
        query = query.where(OrgPrivateAsset.type == asset_type.value)

    if enabled_only:
        query = query.where(OrgPrivateAsset.enabled == True)  # noqa: E712

    result = await db.execute(query)
    return list(result.scalars().all())


async def get_org_asset_by_slug(
    db: AsyncSession,
    org_id: str,
    slug: str,
) -> Optional[OrgPrivateAsset]:
    """Get an org-private asset by slug."""
    result = await db.execute(
        select(OrgPrivateAsset).where(
            and_(
                OrgPrivateAsset.org_id == org_id,
                OrgPrivateAsset.slug == slug,
            )
        )
    )
    return result.scalar_one_or_none()


async def get_org_asset_by_id(
    db: AsyncSession,
    asset_id: UUID,
) -> Optional[OrgPrivateAsset]:
    """Get an org-private asset by ID."""
    result = await db.execute(
        select(OrgPrivateAsset).where(OrgPrivateAsset.id == asset_id)
    )
    return result.scalar_one_or_none()


async def create_org_asset(
    db: AsyncSession,
    org_id: str,
    user_id: str,
    slug: str,
    name: str,
    asset_type: AssetType,
    content: dict,
    description: Optional[str] = None,
    config_schema: Optional[dict] = None,
) -> OrgPrivateAsset:
    """Create an org-private asset."""
    # Check slug uniqueness within org
    existing = await get_org_asset_by_slug(db, org_id, slug)
    if existing:
        raise SlugConflictError(f"Asset slug '{slug}' already exists for this organization")

    asset = OrgPrivateAsset(
        org_id=org_id,
        type=asset_type.value,
        slug=slug,
        name=name,
        description=description,
        content=content,
        config_schema=config_schema,
        created_by_user_id=user_id,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    return asset


async def update_org_asset(
    db: AsyncSession,
    asset: OrgPrivateAsset,
    name: Optional[str] = None,
    description: Optional[str] = None,
    content: Optional[dict] = None,
    config_schema: Optional[dict] = None,
    enabled: Optional[bool] = None,
) -> OrgPrivateAsset:
    """Update an org-private asset."""
    if name is not None:
        asset.name = name
    if description is not None:
        asset.description = description
    if content is not None:
        asset.content = content
    if config_schema is not None:
        asset.config_schema = config_schema
    if enabled is not None:
        asset.enabled = enabled

    await db.flush()
    await db.refresh(asset)
    return asset


async def delete_org_asset(
    db: AsyncSession,
    asset: OrgPrivateAsset,
) -> None:
    """Delete an org-private asset."""
    await db.delete(asset)
    await db.flush()


# =============================================================================
# Tier/Limit Checking
# =============================================================================


async def check_org_can_create_private_assets(
    db: AsyncSession,
    org_slug: str,
) -> bool:
    """Check if organization can create private assets (Pro+ tier)."""
    result = await db.execute(
        select(Organization).where(Organization.slug == org_slug)
    )
    org = result.scalar_one_or_none()
    if not org:
        return False

    # Pro or Enterprise can create private assets
    return org.plan_tier in (PlanTier.PRO, PlanTier.ENTERPRISE)

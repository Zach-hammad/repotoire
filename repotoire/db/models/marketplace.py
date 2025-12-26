"""Marketplace models for AI Skills, Commands & Styles Registry.

This module defines the SQLAlchemy models for the Repotoire Marketplace:
- MarketplacePublisher: Users/orgs who publish assets
- MarketplaceAsset: The main asset entity (skills, commands, styles, etc.)
- MarketplaceAssetVersion: Immutable versioned content
- MarketplaceInstall: User installations
- MarketplaceReview: Ratings and reviews
- OrgPrivateAsset: Org-only private assets
- AssetSecurityReview: Security review status for asset versions
- AssetReport: Community reports for published assets
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, UUIDPrimaryKeyMixin, generate_repr

if TYPE_CHECKING:
    from .organization import Organization
    from .user import User


# =============================================================================
# Enums (Python side only - CHECK constraints used in DB for flexibility)
# =============================================================================


class PublisherType(str, enum.Enum):
    """Type of publisher."""

    USER = "user"
    ORGANIZATION = "organization"


class AssetType(str, enum.Enum):
    """Type of marketplace asset."""

    SKILL = "skill"  # MCP skills
    COMMAND = "command"  # Slash commands
    STYLE = "style"  # Claude styles/personas
    HOOK = "hook"  # Lifecycle hooks
    PROMPT = "prompt"  # Reusable prompts


class PricingType(str, enum.Enum):
    """Pricing model for assets."""

    FREE = "free"  # Free for everyone
    PRO = "pro"  # Requires Repotoire Pro
    PAID = "paid"  # One-time or subscription payment


class AssetVisibility(str, enum.Enum):
    """Visibility level for assets."""

    PUBLIC = "public"  # Visible in marketplace
    PRIVATE = "private"  # Only visible to owner/org
    UNLISTED = "unlisted"  # Accessible via link, not in search


# =============================================================================
# MarketplacePublisher - Users/orgs who publish assets
# =============================================================================


class MarketplacePublisher(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Publisher model representing a user or organization that publishes assets.

    Publishers are the entities that can create and manage marketplace assets.
    A publisher can be either an individual user or an organization.

    Attributes:
        id: UUID primary key
        type: Publisher type (user or organization)
        clerk_user_id: Clerk user ID (for user publishers)
        clerk_org_id: Clerk organization ID (for org publishers)
        slug: Unique URL-friendly identifier (e.g., @acme)
        display_name: Display name shown in marketplace
        description: Publisher bio/about text
        avatar_url: Profile image URL
        website_url: Publisher website
        github_url: GitHub profile/org URL
        verified_at: When publisher was verified (null if not)
        created_at: When the publisher was created
        updated_at: When the publisher was last updated
        assets: List of assets published by this publisher
    """

    __tablename__ = "marketplace_publishers"

    type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    clerk_user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    clerk_org_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    avatar_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    website_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    github_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Stripe Connect integration
    stripe_account_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )
    stripe_onboarding_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    stripe_charges_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    stripe_payouts_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )

    # Relationships
    assets: Mapped[List["MarketplaceAsset"]] = relationship(
        "MarketplaceAsset",
        back_populates="publisher",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('user', 'organization')",
            name="ck_marketplace_publishers_type",
        ),
        # Either clerk_user_id OR clerk_org_id must be set based on type
        CheckConstraint(
            "(type = 'user' AND clerk_user_id IS NOT NULL AND clerk_org_id IS NULL) OR "
            "(type = 'organization' AND clerk_org_id IS NOT NULL AND clerk_user_id IS NULL)",
            name="ck_marketplace_publishers_clerk_id",
        ),
        Index("ix_marketplace_publishers_slug", "slug"),
    )

    @property
    def is_verified(self) -> bool:
        """Check if the publisher is verified."""
        return self.verified_at is not None

    def __repr__(self) -> str:
        return generate_repr(self, "id", "slug", "type")


# =============================================================================
# MarketplaceAsset - The main asset entity
# =============================================================================


class MarketplaceAsset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Asset model representing a marketplace item (skill, command, style, etc.).

    Assets are the core entities in the marketplace. Each asset belongs to a
    publisher and can have multiple versions.

    Attributes:
        id: UUID primary key
        publisher_id: Foreign key to the publisher
        type: Asset type (skill, command, style, hook, prompt)
        slug: URL-friendly identifier unique within publisher
        name: Display name
        description: Short description (for cards/lists)
        readme: Full markdown documentation
        icon_url: Asset icon/logo URL
        tags: Array of tags for categorization
        pricing_type: Pricing model (free, pro, paid)
        price_cents: Price in cents (for paid assets)
        visibility: Visibility level (public, private, unlisted)
        published_at: When first published (null if draft)
        featured_at: When featured (null if not)
        deprecated_at: When deprecated (null if not)
        install_count: Denormalized install count
        rating_avg: Denormalized average rating (1-5)
        rating_count: Denormalized review count
        asset_metadata: Flexible JSON metadata (DB column: 'metadata')
        created_at: When the asset was created
        updated_at: When the asset was last updated
        publisher: The publisher of this asset
        versions: List of versions for this asset
        installs: List of installations of this asset
        reviews: List of reviews for this asset
    """

    __tablename__ = "marketplace_assets"

    publisher_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_publishers.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    readme: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    icon_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    tags: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String(50)),
        nullable=True,
    )
    pricing_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="free",
    )
    price_cents: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )
    visibility: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="public",
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    featured_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deprecated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Denormalized stats (updated via triggers or app logic)
    install_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    rating_avg: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(3, 2),  # 0.00 to 5.00
        nullable=True,
    )
    rating_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    # Flexible metadata (e.g., required permissions, compatibility info)
    # Note: Named 'asset_metadata' to avoid conflict with SQLAlchemy's reserved 'metadata'
    asset_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata",  # Column name in DB is still 'metadata'
        JSONB,
        nullable=True,
    )

    # Relationships
    publisher: Mapped["MarketplacePublisher"] = relationship(
        "MarketplacePublisher",
        back_populates="assets",
    )
    versions: Mapped[List["MarketplaceAssetVersion"]] = relationship(
        "MarketplaceAssetVersion",
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="desc(MarketplaceAssetVersion.created_at)",
    )
    installs: Mapped[List["MarketplaceInstall"]] = relationship(
        "MarketplaceInstall",
        back_populates="asset",
        cascade="all, delete-orphan",
    )
    reviews: Mapped[List["MarketplaceReview"]] = relationship(
        "MarketplaceReview",
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="desc(MarketplaceReview.created_at)",
    )
    purchases: Mapped[List["MarketplacePurchase"]] = relationship(
        "MarketplacePurchase",
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="desc(MarketplacePurchase.created_at)",
    )
    reports: Mapped[List["AssetReport"]] = relationship(
        "AssetReport",
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="desc(AssetReport.created_at)",
    )

    __table_args__ = (
        # Unique slug within publisher
        UniqueConstraint("publisher_id", "slug", name="uq_marketplace_assets_publisher_slug"),
        # Type validation
        CheckConstraint(
            "type IN ('skill', 'command', 'style', 'hook', 'prompt')",
            name="ck_marketplace_assets_type",
        ),
        # Pricing type validation
        CheckConstraint(
            "pricing_type IN ('free', 'pro', 'paid')",
            name="ck_marketplace_assets_pricing_type",
        ),
        # Visibility validation
        CheckConstraint(
            "visibility IN ('public', 'private', 'unlisted')",
            name="ck_marketplace_assets_visibility",
        ),
        # Price is required for paid assets
        CheckConstraint(
            "(pricing_type != 'paid') OR (price_cents IS NOT NULL AND price_cents > 0)",
            name="ck_marketplace_assets_paid_price",
        ),
        # Rating validation
        CheckConstraint(
            "rating_avg IS NULL OR (rating_avg >= 0 AND rating_avg <= 5)",
            name="ck_marketplace_assets_rating_range",
        ),
        # Indexes
        Index("ix_marketplace_assets_publisher_id", "publisher_id"),
        Index("ix_marketplace_assets_type", "type"),
        Index("ix_marketplace_assets_visibility", "visibility"),
        Index("ix_marketplace_assets_published_at", "published_at"),
        Index("ix_marketplace_assets_featured_at", "featured_at"),
        Index("ix_marketplace_assets_install_count", "install_count"),
        Index("ix_marketplace_assets_rating_avg", "rating_avg"),
        # GIN index for tags array search
        Index("ix_marketplace_assets_tags", "tags", postgresql_using="gin"),
    )

    @property
    def is_published(self) -> bool:
        """Check if the asset is published."""
        return self.published_at is not None

    @property
    def is_featured(self) -> bool:
        """Check if the asset is featured."""
        return self.featured_at is not None

    @property
    def is_deprecated(self) -> bool:
        """Check if the asset is deprecated."""
        return self.deprecated_at is not None

    @property
    def latest_version(self) -> Optional["MarketplaceAssetVersion"]:
        """Get the latest published version."""
        for version in self.versions:
            if version.published_at is not None and version.yanked_at is None:
                return version
        return None

    def __repr__(self) -> str:
        return generate_repr(self, "id", "slug", "type", "visibility")


# =============================================================================
# MarketplaceAssetVersion - Immutable versioned content
# =============================================================================


class MarketplaceAssetVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Version model representing an immutable version of an asset.

    Each asset can have multiple versions. Versions are immutable once
    published - updates require creating a new version.

    Attributes:
        id: UUID primary key
        asset_id: Foreign key to the parent asset
        version: Semantic version string (e.g., "1.0.0")
        changelog: What changed in this version
        content: The actual asset content (JSON)
        source_url: Optional link to source (e.g., GitHub)
        checksum: SHA256 hash of content for integrity
        min_repotoire_version: Minimum compatible Repotoire version
        max_repotoire_version: Maximum compatible Repotoire version
        download_count: Number of times this version was downloaded
        published_at: When this version was published
        yanked_at: When this version was yanked (null if not)
        yank_reason: Why the version was yanked
        dependencies: Map of asset slugs to version constraints (e.g., {"@pub/name": "^1.0.0"})
        created_at: When the version was created
        updated_at: When the version was last updated
        asset: The parent asset
        installs: List of installations using this version
    """

    __tablename__ = "marketplace_asset_versions"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    changelog: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    content: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    source_url: Mapped[Optional[str]] = mapped_column(
        String(2048),
        nullable=True,
    )
    checksum: Mapped[str] = mapped_column(
        String(64),  # SHA256 hex
        nullable=False,
    )
    min_repotoire_version: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )
    max_repotoire_version: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
    )
    download_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    yanked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    yank_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    dependencies: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        server_default="{}",
        comment="Map of asset slug to version constraint",
    )

    # Relationships
    asset: Mapped["MarketplaceAsset"] = relationship(
        "MarketplaceAsset",
        back_populates="versions",
    )
    installs: Mapped[List["MarketplaceInstall"]] = relationship(
        "MarketplaceInstall",
        back_populates="version",
    )
    security_reviews: Mapped[List["AssetSecurityReview"]] = relationship(
        "AssetSecurityReview",
        back_populates="asset_version",
        cascade="all, delete-orphan",
        order_by="desc(AssetSecurityReview.created_at)",
    )

    __table_args__ = (
        # Unique version per asset
        UniqueConstraint("asset_id", "version", name="uq_marketplace_asset_versions_asset_version"),
        # Indexes
        Index("ix_marketplace_asset_versions_asset_id", "asset_id"),
        Index("ix_marketplace_asset_versions_published_at", "published_at"),
        Index("ix_marketplace_asset_versions_yanked_at", "yanked_at"),
    )

    @property
    def is_published(self) -> bool:
        """Check if the version is published."""
        return self.published_at is not None

    @property
    def is_yanked(self) -> bool:
        """Check if the version is yanked."""
        return self.yanked_at is not None

    def __repr__(self) -> str:
        return generate_repr(self, "id", "version", "asset_id")


# =============================================================================
# MarketplaceInstall - User installations
# =============================================================================


class MarketplaceInstall(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Install model representing a user's installation of an asset.

    Tracks which users have installed which assets and versions,
    along with any custom configuration.

    Attributes:
        id: UUID primary key
        user_id: Clerk user ID who installed
        asset_id: Foreign key to the installed asset
        version_id: Foreign key to the installed version
        config: User's custom configuration (JSON)
        enabled: Whether the installation is currently enabled
        auto_update: Whether to auto-update to new versions
        created_at: When the installation was created (install date)
        updated_at: When the installation was last updated
        asset: The installed asset
        version: The installed version
    """

    __tablename__ = "marketplace_installs"

    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_asset_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    config: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    auto_update: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )

    # Relationships
    asset: Mapped["MarketplaceAsset"] = relationship(
        "MarketplaceAsset",
        back_populates="installs",
    )
    version: Mapped[Optional["MarketplaceAssetVersion"]] = relationship(
        "MarketplaceAssetVersion",
        back_populates="installs",
    )

    __table_args__ = (
        # One installation per user per asset
        UniqueConstraint("user_id", "asset_id", name="uq_marketplace_installs_user_asset"),
        # Indexes
        Index("ix_marketplace_installs_user_id", "user_id"),
        Index("ix_marketplace_installs_asset_id", "asset_id"),
        Index("ix_marketplace_installs_version_id", "version_id"),
        Index("ix_marketplace_installs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "user_id", "asset_id")


# =============================================================================
# MarketplaceReview - Ratings and reviews
# =============================================================================


class MarketplaceReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Review model representing a user's rating and review of an asset.

    Users can rate assets 1-5 stars and optionally leave a text review.
    One review per user per asset.

    Attributes:
        id: UUID primary key
        user_id: Clerk user ID who wrote the review
        asset_id: Foreign key to the reviewed asset
        rating: Rating 1-5
        title: Optional review title
        body: Optional review body text
        helpful_count: Number of users who found this helpful
        reported_at: When the review was reported (null if not)
        hidden_at: When the review was hidden by moderation
        created_at: When the review was created
        updated_at: When the review was last updated
        asset: The reviewed asset
    """

    __tablename__ = "marketplace_reviews"

    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    title: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    body: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    helpful_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    reported_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    hidden_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    asset: Mapped["MarketplaceAsset"] = relationship(
        "MarketplaceAsset",
        back_populates="reviews",
    )

    __table_args__ = (
        # One review per user per asset
        UniqueConstraint("user_id", "asset_id", name="uq_marketplace_reviews_user_asset"),
        # Rating must be 1-5
        CheckConstraint(
            "rating >= 1 AND rating <= 5",
            name="ck_marketplace_reviews_rating_range",
        ),
        # Indexes
        Index("ix_marketplace_reviews_user_id", "user_id"),
        Index("ix_marketplace_reviews_asset_id", "asset_id"),
        Index("ix_marketplace_reviews_rating", "rating"),
        Index("ix_marketplace_reviews_created_at", "created_at"),
        Index("ix_marketplace_reviews_hidden_at", "hidden_at"),
    )

    @property
    def is_hidden(self) -> bool:
        """Check if the review is hidden."""
        return self.hidden_at is not None

    def __repr__(self) -> str:
        return generate_repr(self, "id", "user_id", "asset_id", "rating")


# =============================================================================
# OrgPrivateAsset - Org-only private assets
# =============================================================================


class OrgPrivateAsset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Model for organization-private assets not listed in public marketplace.

    These are assets that are only available to members of a specific
    organization, useful for internal tools, custom prompts, etc.

    Attributes:
        id: UUID primary key
        org_id: Clerk organization ID
        type: Asset type (skill, command, style, hook, prompt)
        slug: URL-friendly identifier unique within org
        name: Display name
        description: Short description
        content: The actual asset content (JSON)
        config_schema: JSON Schema for configuration (optional)
        created_by_user_id: Clerk user ID who created
        enabled: Whether the asset is enabled for org members
        created_at: When the asset was created
        updated_at: When the asset was last updated
    """

    __tablename__ = "org_private_assets"

    org_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    content: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )
    config_schema: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )

    __table_args__ = (
        # Unique slug within org
        UniqueConstraint("org_id", "slug", name="uq_org_private_assets_org_slug"),
        # Type validation
        CheckConstraint(
            "type IN ('skill', 'command', 'style', 'hook', 'prompt')",
            name="ck_org_private_assets_type",
        ),
        # Indexes
        Index("ix_org_private_assets_org_id", "org_id"),
        Index("ix_org_private_assets_type", "type"),
        Index("ix_org_private_assets_created_by_user_id", "created_by_user_id"),
        Index("ix_org_private_assets_enabled", "enabled"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "org_id", "slug", "type")


# =============================================================================
# PurchaseStatus - Status of a marketplace purchase
# =============================================================================


class PurchaseStatus(str, enum.Enum):
    """Status of a marketplace purchase."""

    PENDING = "pending"  # Payment initiated, awaiting completion
    COMPLETED = "completed"  # Payment successful, asset installed
    FAILED = "failed"  # Payment failed
    REFUNDED = "refunded"  # Purchase was refunded


# =============================================================================
# MarketplacePurchase - Record of paid asset purchases
# =============================================================================


class MarketplacePurchase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Purchase model representing a paid asset purchase.

    Tracks purchases of paid assets through Stripe Connect, including
    the platform fee split and payment status.

    Attributes:
        id: UUID primary key
        asset_id: Foreign key to the purchased asset
        user_id: Clerk user ID who made the purchase
        amount_cents: Total amount paid in cents
        platform_fee_cents: Platform fee (15%) in cents
        creator_share_cents: Creator's share (85%) in cents
        currency: Currency code (e.g., "usd")
        stripe_payment_intent_id: Stripe PaymentIntent ID
        stripe_charge_id: Stripe Charge ID (after successful payment)
        status: Purchase status (pending, completed, failed, refunded)
        completed_at: When the purchase was completed
        refunded_at: When the purchase was refunded (null if not)
        refund_reason: Reason for refund
        created_at: When the purchase was created
        updated_at: When the purchase was last updated
        asset: The purchased asset
    """

    __tablename__ = "marketplace_purchases"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    amount_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    platform_fee_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    creator_share_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        server_default="usd",
    )
    stripe_payment_intent_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )
    stripe_charge_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="pending",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    refunded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    refund_reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    asset: Mapped["MarketplaceAsset"] = relationship(
        "MarketplaceAsset",
        back_populates="purchases",
    )

    __table_args__ = (
        # One purchase per user per asset (can re-purchase after refund via different row)
        UniqueConstraint("asset_id", "user_id", name="uq_marketplace_purchases_asset_user"),
        # Status validation
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed', 'refunded')",
            name="ck_marketplace_purchases_status",
        ),
        # Amount validation
        CheckConstraint(
            "amount_cents > 0",
            name="ck_marketplace_purchases_amount_positive",
        ),
        CheckConstraint(
            "platform_fee_cents >= 0",
            name="ck_marketplace_purchases_fee_positive",
        ),
        CheckConstraint(
            "creator_share_cents >= 0",
            name="ck_marketplace_purchases_share_positive",
        ),
        # Indexes
        Index("ix_marketplace_purchases_asset_id", "asset_id"),
        Index("ix_marketplace_purchases_user_id", "user_id"),
        Index("ix_marketplace_purchases_status", "status"),
        Index("ix_marketplace_purchases_created_at", "created_at"),
    )

    @property
    def is_completed(self) -> bool:
        """Check if the purchase is completed."""
        return self.status == PurchaseStatus.COMPLETED.value

    @property
    def is_refunded(self) -> bool:
        """Check if the purchase is refunded."""
        return self.status == PurchaseStatus.REFUNDED.value

    def __repr__(self) -> str:
        return generate_repr(self, "id", "user_id", "asset_id", "status")


# =============================================================================
# AssetReviewStatus - Status of security review
# =============================================================================


class AssetReviewStatus(str, enum.Enum):
    """Status of an asset security review."""

    PENDING = "pending"  # Awaiting review
    IN_REVIEW = "in_review"  # Being reviewed by admin
    APPROVED = "approved"  # Approved for publication
    REJECTED = "rejected"  # Rejected due to policy/security
    REQUIRES_CHANGES = "requires_changes"  # Changes requested


# =============================================================================
# ReportReason - Reason for community reports
# =============================================================================


class ReportReason(str, enum.Enum):
    """Reason for reporting an asset."""

    MALICIOUS = "malicious"  # Contains malware or exploits
    BROKEN = "broken"  # Doesn't work as described
    INAPPROPRIATE = "inappropriate"  # Violates terms of service
    COPYRIGHT = "copyright"  # IP/copyright violation
    SPAM = "spam"  # Spam or misleading content
    OTHER = "other"  # Other reason


# =============================================================================
# ReportStatus - Status of a community report
# =============================================================================


class ReportStatus(str, enum.Enum):
    """Status of a community report."""

    OPEN = "open"  # New report
    INVESTIGATING = "investigating"  # Being investigated
    RESOLVED = "resolved"  # Issue resolved
    DISMISSED = "dismissed"  # Report dismissed


# =============================================================================
# AssetSecurityReview - Security review for asset versions
# =============================================================================


class AssetSecurityReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Security review for an asset version.

    Tracks automated scan results and manual review status for
    marketplace assets. Each version goes through a review process
    before publication.

    Attributes:
        id: UUID primary key
        asset_version_id: Foreign key to the asset version
        reviewer_id: Clerk user ID of reviewer (null = automated)
        status: Review status (pending, in_review, approved, rejected, requires_changes)
        scan_findings: JSONB array of scan findings from AssetScanner
        scan_verdict: Automated scan verdict (approved, rejected, pending_review)
        scanned_at: When the automated scan was run
        reviewer_notes: Notes from manual review
        reviewed_at: When manual review was completed
        changes_requested: List of requested changes (for requires_changes status)
        created_at: When the review was created
        updated_at: When the review was last updated
        asset_version: The asset version being reviewed
    """

    __tablename__ = "asset_security_reviews"

    asset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_asset_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    reviewer_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Clerk user ID (null = automated review)",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="pending",
    )

    # Automated scan results
    scan_findings: Mapped[Optional[List]] = mapped_column(
        JSONB,
        nullable=True,
        server_default="[]",
        comment="Array of scan findings from AssetScanner",
    )
    scan_verdict: Mapped[Optional[str]] = mapped_column(
        String(30),
        nullable=True,
        comment="approved, rejected, pending_review, approved_with_warnings",
    )
    scanned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Manual review
    reviewer_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    changes_requested: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(Text),
        nullable=True,
        comment="List of requested changes for requires_changes status",
    )

    # Relationships
    asset_version: Mapped["MarketplaceAssetVersion"] = relationship(
        "MarketplaceAssetVersion",
        back_populates="security_reviews",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_review', 'approved', 'rejected', 'requires_changes')",
            name="ck_asset_security_reviews_status",
        ),
        Index("ix_asset_security_reviews_asset_version_id", "asset_version_id"),
        Index("ix_asset_security_reviews_status", "status"),
        Index("ix_asset_security_reviews_reviewer_id", "reviewer_id"),
        Index("ix_asset_security_reviews_scanned_at", "scanned_at"),
    )

    @property
    def is_approved(self) -> bool:
        """Check if the review is approved."""
        return self.status == AssetReviewStatus.APPROVED.value

    @property
    def is_rejected(self) -> bool:
        """Check if the review is rejected."""
        return self.status == AssetReviewStatus.REJECTED.value

    @property
    def is_pending(self) -> bool:
        """Check if the review is pending."""
        return self.status == AssetReviewStatus.PENDING.value

    def __repr__(self) -> str:
        return generate_repr(self, "id", "asset_version_id", "status")


# =============================================================================
# AssetReport - Community reports for published assets
# =============================================================================


class AssetReport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Community report for a published asset.

    Allows users to report assets for various policy violations.
    Multiple reports trigger admin alerts.

    Attributes:
        id: UUID primary key
        asset_id: Foreign key to the reported asset
        reporter_id: Clerk user ID of reporter
        reason: Report reason (malicious, broken, inappropriate, copyright, spam, other)
        description: Additional details from reporter
        status: Report status (open, investigating, resolved, dismissed)
        resolution_notes: Notes from admin resolution
        resolved_by: Clerk user ID of admin who resolved
        resolved_at: When the report was resolved
        created_at: When the report was created
        updated_at: When the report was last updated
        asset: The reported asset
    """

    __tablename__ = "asset_reports"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    reporter_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Clerk user ID of the reporter",
    )
    reason: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="open",
    )
    resolution_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    resolved_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Clerk user ID of admin who resolved",
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    asset: Mapped["MarketplaceAsset"] = relationship(
        "MarketplaceAsset",
        back_populates="reports",
    )

    __table_args__ = (
        CheckConstraint(
            "reason IN ('malicious', 'broken', 'inappropriate', 'copyright', 'spam', 'other')",
            name="ck_asset_reports_reason",
        ),
        CheckConstraint(
            "status IN ('open', 'investigating', 'resolved', 'dismissed')",
            name="ck_asset_reports_status",
        ),
        # Prevent duplicate reports from same user for same asset
        UniqueConstraint(
            "asset_id", "reporter_id", name="uq_asset_reports_asset_reporter"
        ),
        Index("ix_asset_reports_asset_id", "asset_id"),
        Index("ix_asset_reports_reporter_id", "reporter_id"),
        Index("ix_asset_reports_status", "status"),
        Index("ix_asset_reports_reason", "reason"),
        Index("ix_asset_reports_created_at", "created_at"),
    )

    @property
    def is_open(self) -> bool:
        """Check if the report is open."""
        return self.status == ReportStatus.OPEN.value

    @property
    def is_resolved(self) -> bool:
        """Check if the report is resolved."""
        return self.status == ReportStatus.RESOLVED.value

    def __repr__(self) -> str:
        return generate_repr(self, "id", "asset_id", "reason", "status")


# =============================================================================
# EventType - Type of analytics event
# =============================================================================


class EventType(str, enum.Enum):
    """Type of analytics event."""

    DOWNLOAD = "download"  # Asset version downloaded
    INSTALL = "install"  # Asset installed by user
    UNINSTALL = "uninstall"  # Asset uninstalled by user
    UPDATE = "update"  # Asset updated to new version


# =============================================================================
# AssetEvent - Individual analytics events
# =============================================================================


class AssetEvent(Base, UUIDPrimaryKeyMixin):
    """Individual event tracking for marketplace assets.

    Tracks downloads, installs, uninstalls, and updates with metadata
    about the client and context.

    Attributes:
        id: UUID primary key
        asset_id: Foreign key to the asset
        asset_version_id: Foreign key to the specific version (optional)
        user_id: Clerk user ID (null for anonymous)
        event_type: Type of event (download, install, uninstall, update)
        cli_version: Version of Repotoire CLI used
        os_platform: Operating system (darwin, linux, win32)
        source: Event source (cli, web, api)
        event_metadata: Additional context (DB column: 'metadata')
        created_at: When the event occurred
    """

    __tablename__ = "asset_events"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_version_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("marketplace_asset_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    cli_version: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    os_platform: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    source: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
    )
    event_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    asset: Mapped["MarketplaceAsset"] = relationship(
        "MarketplaceAsset",
        foreign_keys=[asset_id],
    )
    version: Mapped[Optional["MarketplaceAssetVersion"]] = relationship(
        "MarketplaceAssetVersion",
        foreign_keys=[asset_version_id],
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('download', 'install', 'uninstall', 'update')",
            name="ck_asset_events_event_type",
        ),
        Index("ix_asset_events_asset_id", "asset_id"),
        Index("ix_asset_events_user_id", "user_id"),
        Index("ix_asset_events_event_type", "event_type"),
        Index("ix_asset_events_created_at", "created_at"),
        Index("ix_asset_events_asset_event_created", "asset_id", "event_type", "created_at"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "asset_id", "event_type", "created_at")


# =============================================================================
# AssetStats - Aggregated totals per asset
# =============================================================================


class AssetStats(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Aggregated statistics for a marketplace asset.

    Stores denormalized totals for fast dashboard queries.
    Updated by analytics service on each event.

    Attributes:
        id: UUID primary key
        asset_id: Foreign key to the asset (unique)
        total_downloads: Lifetime download count
        total_installs: Lifetime install count
        total_uninstalls: Lifetime uninstall count
        total_updates: Lifetime update count
        active_installs: Current active installs (installs - uninstalls)
        rating_avg: Average rating (mirrored from asset)
        rating_count: Total review count
        total_revenue_cents: Lifetime revenue in cents
        total_purchases: Lifetime purchase count
        downloads_7d: Downloads in last 7 days
        downloads_30d: Downloads in last 30 days
        installs_7d: Installs in last 7 days
        installs_30d: Installs in last 30 days
    """

    __tablename__ = "asset_stats"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Lifetime totals
    total_downloads: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    total_installs: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    total_uninstalls: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    total_updates: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    active_installs: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    # Rating stats
    rating_avg: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(3, 2),
        nullable=True,
    )
    rating_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    # Revenue stats
    total_revenue_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    total_purchases: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    # Rolling windows
    downloads_7d: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    downloads_30d: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    installs_7d: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    installs_30d: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    # Relationships
    asset: Mapped["MarketplaceAsset"] = relationship(
        "MarketplaceAsset",
        foreign_keys=[asset_id],
    )

    __table_args__ = (
        Index("ix_asset_stats_asset_id", "asset_id"),
        Index("ix_asset_stats_active_installs", "active_installs"),
        Index("ix_asset_stats_total_downloads", "total_downloads"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "asset_id", "active_installs")


# =============================================================================
# AssetStatsDaily - Daily snapshots for trend charts
# =============================================================================


class AssetStatsDaily(Base, UUIDPrimaryKeyMixin):
    """Daily statistics snapshot for trend charts.

    One row per asset per day. Created by nightly aggregation job.

    Attributes:
        id: UUID primary key
        asset_id: Foreign key to the asset
        date: The date for this snapshot
        downloads: Downloads on this day
        installs: Installs on this day
        uninstalls: Uninstalls on this day
        updates: Updates on this day
        cumulative_downloads: Running total through this day
        cumulative_installs: Running total through this day
        active_installs: Active installs at end of day
        revenue_cents: Revenue earned on this day
        purchases: Purchases on this day
        unique_users: Unique users who interacted
        created_at: When this snapshot was created
    """

    __tablename__ = "asset_stats_daily"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_assets.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Daily counts
    downloads: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    installs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    uninstalls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updates: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Cumulative totals
    cumulative_downloads: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    cumulative_installs: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    active_installs: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    # Revenue
    revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    purchases: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Unique users
    unique_users: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    asset: Mapped["MarketplaceAsset"] = relationship(
        "MarketplaceAsset",
        foreign_keys=[asset_id],
    )

    __table_args__ = (
        UniqueConstraint("asset_id", "date", name="uq_asset_stats_daily_asset_date"),
        Index("ix_asset_stats_daily_asset_id", "asset_id"),
        Index("ix_asset_stats_daily_date", "date"),
        Index("ix_asset_stats_daily_asset_date", "asset_id", "date"),
    )

    def __repr__(self) -> str:
        return generate_repr(self, "id", "asset_id", "date")


# =============================================================================
# PublisherStats - Aggregated stats per publisher
# =============================================================================


class PublisherStats(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Aggregated statistics for a marketplace publisher.

    Stores denormalized totals across all publisher's assets.

    Attributes:
        id: UUID primary key
        publisher_id: Foreign key to the publisher (unique)
        total_assets: Number of published assets
        total_downloads: Lifetime downloads across all assets
        total_installs: Lifetime installs across all assets
        total_active_installs: Current active installs
        total_revenue_cents: Lifetime revenue in cents
        avg_rating: Average rating across all assets
        total_reviews: Total review count
        downloads_7d: Downloads in last 7 days
        downloads_30d: Downloads in last 30 days
    """

    __tablename__ = "publisher_stats"

    publisher_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_publishers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Totals
    total_assets: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_downloads: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_installs: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_active_installs: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    total_revenue_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    # Rating
    avg_rating: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 2), nullable=True)
    total_reviews: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Rolling windows
    downloads_7d: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    downloads_30d: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Relationships
    publisher: Mapped["MarketplacePublisher"] = relationship(
        "MarketplacePublisher",
        foreign_keys=[publisher_id],
    )

    __table_args__ = (Index("ix_publisher_stats_publisher_id", "publisher_id"),)

    def __repr__(self) -> str:
        return generate_repr(self, "id", "publisher_id", "total_downloads")

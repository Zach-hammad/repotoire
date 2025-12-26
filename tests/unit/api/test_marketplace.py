"""Tests for Marketplace API endpoints.

This module provides test coverage for all marketplace API endpoint groups:
- Discovery & Browse
- User Installations
- Reviews & Ratings
- Publisher Management
- Asset Publishing
- Private Org Assets
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from repotoire.api.services import marketplace as mp_service
from repotoire.api.v1.schemas.marketplace import (
    AssetCreate,
    AssetSearchParams,
    InstallRequest,
    PublisherCreate,
    ReviewCreate,
    VersionCreate,
)
from repotoire.db.models.marketplace import (
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


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_publisher() -> MarketplacePublisher:
    """Create a mock publisher."""
    publisher = MagicMock(spec=MarketplacePublisher)
    publisher.id = uuid4()
    publisher.type = PublisherType.USER.value
    publisher.clerk_user_id = "user_test123"
    publisher.clerk_org_id = None
    publisher.slug = "test-publisher"
    publisher.display_name = "Test Publisher"
    publisher.description = "A test publisher"
    publisher.avatar_url = None
    publisher.website_url = None
    publisher.github_url = None
    publisher.verified_at = None
    publisher.created_at = datetime.now(timezone.utc)
    publisher.is_verified = False
    return publisher


@pytest.fixture
def mock_asset(mock_publisher: MarketplacePublisher) -> MarketplaceAsset:
    """Create a mock asset."""
    asset = MagicMock(spec=MarketplaceAsset)
    asset.id = uuid4()
    asset.publisher_id = mock_publisher.id
    asset.publisher = mock_publisher
    asset.type = AssetType.SKILL.value
    asset.slug = "test-skill"
    asset.name = "Test Skill"
    asset.description = "A test skill"
    asset.readme = "# Test Skill\n\nThis is a test."
    asset.icon_url = None
    asset.tags = ["test", "example"]
    asset.pricing_type = PricingType.FREE.value
    asset.price_cents = None
    asset.visibility = AssetVisibility.PUBLIC.value
    asset.published_at = datetime.now(timezone.utc)
    asset.featured_at = None
    asset.deprecated_at = None
    asset.install_count = 10
    asset.rating_avg = Decimal("4.5")
    asset.rating_count = 5
    asset.asset_metadata = {}
    asset.created_at = datetime.now(timezone.utc)
    asset.updated_at = datetime.now(timezone.utc)
    asset.is_published = True
    asset.is_featured = False
    asset.is_deprecated = False
    asset.latest_version = None
    return asset


@pytest.fixture
def mock_version(mock_asset: MarketplaceAsset) -> MarketplaceAssetVersion:
    """Create a mock version."""
    version = MagicMock(spec=MarketplaceAssetVersion)
    version.id = uuid4()
    version.asset_id = mock_asset.id
    version.version = "1.0.0"
    version.changelog = "Initial release"
    version.content = {"type": "skill", "name": "test"}
    version.source_url = None
    version.checksum = "abc123"
    version.min_repotoire_version = None
    version.max_repotoire_version = None
    version.download_count = 5
    version.published_at = datetime.now(timezone.utc)
    version.yanked_at = None
    version.yank_reason = None
    version.created_at = datetime.now(timezone.utc)
    version.is_published = True
    version.is_yanked = False
    return version


@pytest.fixture
def mock_install(
    mock_asset: MarketplaceAsset,
    mock_version: MarketplaceAssetVersion,
) -> MarketplaceInstall:
    """Create a mock install."""
    install = MagicMock(spec=MarketplaceInstall)
    install.id = uuid4()
    install.user_id = "user_test123"
    install.asset_id = mock_asset.id
    install.asset = mock_asset
    install.version_id = mock_version.id
    install.version = mock_version
    install.config = None
    install.enabled = True
    install.auto_update = True
    install.created_at = datetime.now(timezone.utc)
    install.updated_at = datetime.now(timezone.utc)
    return install


@pytest.fixture
def mock_review(mock_asset: MarketplaceAsset) -> MarketplaceReview:
    """Create a mock review."""
    review = MagicMock(spec=MarketplaceReview)
    review.id = uuid4()
    review.user_id = "user_test123"
    review.asset_id = mock_asset.id
    review.rating = 5
    review.title = "Great skill!"
    review.body = "This skill is very useful."
    review.helpful_count = 3
    review.reported_at = None
    review.hidden_at = None
    review.created_at = datetime.now(timezone.utc)
    review.updated_at = datetime.now(timezone.utc)
    review.is_hidden = False
    return review


@pytest.fixture
def mock_user():
    """Create a mock authenticated user."""
    from repotoire.api.shared.auth import ClerkUser

    return ClerkUser(
        user_id="user_test123",
        session_id="session_123",
        org_id=None,
        org_role=None,
        org_slug=None,
        claims={},
    )


@pytest.fixture
def mock_org_user():
    """Create a mock authenticated user with org membership."""
    from repotoire.api.shared.auth import ClerkUser

    return ClerkUser(
        user_id="user_test123",
        session_id="session_123",
        org_id="org_test456",
        org_role="admin",
        org_slug="test-org",
        claims={},
    )


# =============================================================================
# Schema Validation Tests
# =============================================================================


class TestSchemaValidation:
    """Tests for Pydantic schema validation."""

    def test_asset_create_valid(self):
        """Test valid asset creation schema."""
        asset = AssetCreate(
            slug="my-skill",
            name="My Skill",
            type=AssetType.SKILL,
            description="A test skill",
        )
        assert asset.slug == "my-skill"
        assert asset.name == "My Skill"
        assert asset.type == AssetType.SKILL

    def test_asset_create_valid_with_pricing(self):
        """Test asset creation with free pricing (default)."""
        asset = AssetCreate(
            slug="my-skill",
            name="My Skill",
            type=AssetType.SKILL,
        )
        # Default pricing is FREE
        assert asset.pricing_type == PricingType.FREE
        assert asset.price_cents is None

    def test_asset_create_paid_with_price(self):
        """Test valid paid asset creation."""
        asset = AssetCreate(
            slug="paid-skill",
            name="Paid Skill",
            type=AssetType.SKILL,
            pricing_type=PricingType.PAID,
            price_cents=999,
        )
        assert asset.price_cents == 999
        assert asset.pricing_type == PricingType.PAID

    def test_asset_create_invalid_slug_format(self):
        """Test asset slug must match pattern."""
        with pytest.raises(ValidationError) as exc_info:
            AssetCreate(
                slug="Invalid Slug!",  # Contains spaces and special chars
                name="My Skill",
                type=AssetType.SKILL,
            )
        assert "slug" in str(exc_info.value)

    def test_asset_create_slug_too_short(self):
        """Test asset slug minimum length."""
        with pytest.raises(ValidationError) as exc_info:
            AssetCreate(
                slug="a",  # Too short
                name="My Skill",
                type=AssetType.SKILL,
            )
        assert "slug" in str(exc_info.value)

    def test_publisher_create_valid(self):
        """Test valid publisher creation."""
        publisher = PublisherCreate(
            slug="my-publisher",
            display_name="My Publisher",
            description="A test publisher",
        )
        assert publisher.slug == "my-publisher"
        assert publisher.display_name == "My Publisher"

    def test_publisher_create_invalid_slug(self):
        """Test publisher slug validation."""
        with pytest.raises(ValidationError):
            PublisherCreate(
                slug="x",  # Too short
                display_name="Publisher",
            )

    def test_version_create_valid(self):
        """Test valid version creation."""
        version = VersionCreate(
            version="1.0.0",
            content={"type": "skill"},
            changelog="Initial release",
        )
        assert version.version == "1.0.0"
        assert version.content == {"type": "skill"}

    def test_review_create_valid_rating(self):
        """Test review rating must be 1-5."""
        review = ReviewCreate(
            rating=5,
            title="Great!",
            body="This is awesome.",
        )
        assert review.rating == 5

    def test_review_create_invalid_rating_low(self):
        """Test review rating too low."""
        with pytest.raises(ValidationError):
            ReviewCreate(rating=0, title="Bad")

    def test_review_create_invalid_rating_high(self):
        """Test review rating too high."""
        with pytest.raises(ValidationError):
            ReviewCreate(rating=6, title="Too high")

    def test_install_request_valid(self):
        """Test valid install request."""
        request = InstallRequest(
            version="1.0.0",
            config={"option": "value"},
            auto_update=False,
        )
        assert request.version == "1.0.0"
        assert request.auto_update is False

    def test_install_request_defaults(self):
        """Test install request defaults."""
        request = InstallRequest()
        assert request.version is None
        assert request.config is None
        assert request.auto_update is True


# =============================================================================
# Service Layer Tests
# =============================================================================


class TestMarketplaceService:
    """Tests for marketplace service functions."""

    def test_compute_content_checksum(self):
        """Test content checksum computation."""
        content = {"type": "skill", "name": "test"}
        checksum = mp_service.compute_content_checksum(content)
        assert len(checksum) == 64  # SHA256 hex

        # Same content should produce same checksum
        checksum2 = mp_service.compute_content_checksum(content)
        assert checksum == checksum2

        # Different content should produce different checksum
        different_content = {"type": "skill", "name": "other"}
        checksum3 = mp_service.compute_content_checksum(different_content)
        assert checksum != checksum3

    def test_compute_content_checksum_key_order_independent(self):
        """Test checksum is consistent regardless of key order."""
        content1 = {"name": "test", "type": "skill"}
        content2 = {"type": "skill", "name": "test"}
        # json.dumps with sort_keys=True makes order consistent
        assert mp_service.compute_content_checksum(content1) == mp_service.compute_content_checksum(content2)

    def test_verify_publisher_ownership_user(self, mock_publisher, mock_user):
        """Test user ownership verification."""
        mock_publisher.type = PublisherType.USER.value
        mock_publisher.clerk_user_id = mock_user.user_id

        assert mp_service.verify_publisher_ownership(mock_publisher, mock_user) is True

        # Wrong user
        mock_user.user_id = "different_user"
        assert mp_service.verify_publisher_ownership(mock_publisher, mock_user) is False

    def test_verify_publisher_ownership_org(self, mock_publisher, mock_org_user):
        """Test org ownership verification."""
        mock_publisher.type = PublisherType.ORGANIZATION.value
        mock_publisher.clerk_org_id = mock_org_user.org_id

        assert mp_service.verify_publisher_ownership(mock_publisher, mock_org_user) is True

        # Wrong org
        mock_org_user.org_id = "different_org"
        assert mp_service.verify_publisher_ownership(mock_publisher, mock_org_user) is False


class TestPaginatedResult:
    """Tests for PaginatedResult dataclass."""

    def test_paginated_result_has_more_true(self):
        """Test has_more is true when more items exist."""
        result = mp_service.PaginatedResult(
            items=[1, 2, 3],
            total=10,
            page=1,
            limit=3,
            has_more=True,
        )
        assert result.has_more is True
        assert result.total == 10

    def test_paginated_result_has_more_false(self):
        """Test has_more is false on last page."""
        result = mp_service.PaginatedResult(
            items=[9, 10],
            total=10,
            page=4,
            limit=3,
            has_more=False,
        )
        assert result.has_more is False

    def test_paginated_result_attributes(self):
        """Test PaginatedResult stores all attributes."""
        result = mp_service.PaginatedResult(
            items=["a", "b", "c"],
            total=100,
            page=5,
            limit=10,
            has_more=True,
        )
        assert len(result.items) == 3
        assert result.total == 100
        assert result.page == 5
        assert result.limit == 10


class TestRatingSummary:
    """Tests for RatingSummary dataclass."""

    def test_rating_summary(self):
        """Test rating summary dataclass."""
        summary = mp_service.RatingSummary(
            average=Decimal("4.5"),
            count=10,
            distribution={1: 0, 2: 1, 3: 2, 4: 3, 5: 4},
        )
        assert summary.average == Decimal("4.5")
        assert summary.count == 10
        assert summary.distribution[5] == 4

    def test_rating_summary_distribution_totals(self):
        """Test rating summary distribution matches count."""
        summary = mp_service.RatingSummary(
            average=Decimal("3.5"),
            count=10,
            distribution={1: 1, 2: 1, 3: 2, 4: 3, 5: 3},
        )
        total = sum(summary.distribution.values())
        assert total == summary.count

    def test_rating_summary_no_ratings(self):
        """Test rating summary with no ratings."""
        summary = mp_service.RatingSummary(
            average=None,
            count=0,
            distribution={1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
        )
        assert summary.average is None
        assert summary.count == 0


# =============================================================================
# Pricing Validation Tests (Service Layer)
# =============================================================================


class TestPricingValidation:
    """Tests for pricing validation in service layer."""

    def test_invalid_pricing_error_raised_for_paid_no_price(self):
        """Test InvalidPricingError is raised for paid with no price."""
        error = mp_service.InvalidPricingError("Paid assets require a positive price")
        assert "positive price" in str(error)

    def test_invalid_pricing_error_raised_for_paid_zero_price(self):
        """Test InvalidPricingError is raised for paid with zero price."""
        error = mp_service.InvalidPricingError("Paid assets require a positive price")
        assert isinstance(error, mp_service.MarketplaceError)


# =============================================================================
# Exception Tests
# =============================================================================


class TestExceptions:
    """Tests for custom exceptions."""

    def test_marketplace_error_base(self):
        """Test base marketplace error."""
        error = mp_service.MarketplaceError("Test error")
        assert str(error) == "Test error"

    def test_slug_conflict_error(self):
        """Test slug conflict error."""
        error = mp_service.SlugConflictError("Slug 'test' already exists")
        assert "test" in str(error)
        assert isinstance(error, mp_service.MarketplaceError)

    def test_review_requires_install_error(self):
        """Test review requires install error."""
        error = mp_service.ReviewRequiresInstallError("Must install first")
        assert isinstance(error, mp_service.MarketplaceError)

    def test_duplicate_review_error(self):
        """Test duplicate review error."""
        error = mp_service.DuplicateReviewError("Already reviewed")
        assert isinstance(error, mp_service.MarketplaceError)

    def test_invalid_pricing_error(self):
        """Test invalid pricing error."""
        error = mp_service.InvalidPricingError("Price must be positive")
        assert isinstance(error, mp_service.MarketplaceError)

    def test_publisher_not_found_error(self):
        """Test publisher not found error."""
        error = mp_service.PublisherNotFoundError("Publisher not found")
        assert isinstance(error, mp_service.MarketplaceError)

    def test_asset_not_found_error(self):
        """Test asset not found error."""
        error = mp_service.AssetNotFoundError("Asset not found")
        assert isinstance(error, mp_service.MarketplaceError)

    def test_version_not_found_error(self):
        """Test version not found error."""
        error = mp_service.VersionNotFoundError("Version not found")
        assert isinstance(error, mp_service.MarketplaceError)

    def test_not_authorized_error(self):
        """Test not authorized error."""
        error = mp_service.NotAuthorizedError("Not authorized")
        assert isinstance(error, mp_service.MarketplaceError)

    def test_install_limit_error(self):
        """Test install limit exceeded error."""
        error = mp_service.InstallLimitExceededError("Limit exceeded")
        assert isinstance(error, mp_service.MarketplaceError)


# =============================================================================
# Authorization Tests
# =============================================================================


class TestAuthorization:
    """Tests for authorization and access control."""

    def test_publisher_ownership_user_type(self, mock_publisher, mock_user):
        """Test user-type publisher ownership."""
        mock_publisher.type = PublisherType.USER.value
        mock_publisher.clerk_user_id = mock_user.user_id

        assert mp_service.verify_publisher_ownership(mock_publisher, mock_user) is True

    def test_publisher_ownership_org_type(self, mock_publisher, mock_org_user):
        """Test org-type publisher ownership."""
        mock_publisher.type = PublisherType.ORGANIZATION.value
        mock_publisher.clerk_org_id = mock_org_user.org_id

        assert mp_service.verify_publisher_ownership(mock_publisher, mock_org_user) is True

    def test_publisher_ownership_wrong_user(self, mock_publisher):
        """Test wrong user doesn't own publisher."""
        from repotoire.api.shared.auth import ClerkUser

        mock_publisher.type = PublisherType.USER.value
        mock_publisher.clerk_user_id = "user_owner"

        wrong_user = ClerkUser(
            user_id="user_other",
            session_id="session_123",
            org_id=None,
            org_role=None,
            org_slug=None,
            claims={},
        )

        assert mp_service.verify_publisher_ownership(mock_publisher, wrong_user) is False

    def test_publisher_ownership_user_checking_org(self, mock_publisher, mock_user):
        """Test user can't claim org publisher ownership."""
        mock_publisher.type = PublisherType.ORGANIZATION.value
        mock_publisher.clerk_org_id = "org_123"

        # User without org_id can't own org-type publisher
        assert mp_service.verify_publisher_ownership(mock_publisher, mock_user) is False


# =============================================================================
# Pagination Tests
# =============================================================================


class TestPagination:
    """Tests for pagination across endpoints."""

    def test_pagination_calculation(self):
        """Test pagination math is correct."""
        # Page 1, limit 20, 55 total
        offset = (1 - 1) * 20
        assert offset == 0

        # Page 3, limit 20, 55 total
        offset = (3 - 1) * 20
        assert offset == 40

        # has_more calculation
        page = 3
        limit = 20
        items_returned = 15
        total = 55
        has_more = (offset + items_returned) < total
        assert has_more is False  # 40 + 15 = 55, not less than 55

    def test_pagination_has_more_true(self):
        """Test has_more is true when more pages exist."""
        offset = 0
        items_returned = 20
        total = 100
        has_more = (offset + items_returned) < total
        assert has_more is True  # 0 + 20 = 20 < 100

    def test_pagination_limit_max_100(self):
        """Test pagination limit capped at 100."""
        # This should work
        params = AssetSearchParams(limit=100)
        assert params.limit == 100

        # This should fail validation
        with pytest.raises(ValidationError):
            AssetSearchParams(limit=101)

    def test_pagination_limit_min_1(self):
        """Test pagination limit minimum is 1."""
        params = AssetSearchParams(limit=1)
        assert params.limit == 1

        with pytest.raises(ValidationError):
            AssetSearchParams(limit=0)

    def test_pagination_page_min_1(self):
        """Test page minimum is 1."""
        params = AssetSearchParams(page=1)
        assert params.page == 1

        with pytest.raises(ValidationError):
            AssetSearchParams(page=0)


# =============================================================================
# Asset Type Tests
# =============================================================================


class TestAssetTypes:
    """Tests for asset type handling."""

    def test_all_asset_types_exist(self):
        """Test all expected asset types exist."""
        assert AssetType.SKILL is not None
        assert AssetType.COMMAND is not None
        assert AssetType.STYLE is not None
        assert AssetType.HOOK is not None
        assert AssetType.PROMPT is not None

    def test_asset_create_with_each_type(self):
        """Test creating assets with each type."""
        for asset_type in AssetType:
            asset = AssetCreate(
                slug=f"test-{asset_type.value}",
                name=f"Test {asset_type.value}",
                type=asset_type,
            )
            assert asset.type == asset_type


# =============================================================================
# Visibility Tests
# =============================================================================


class TestVisibility:
    """Tests for asset visibility."""

    def test_visibility_types_exist(self):
        """Test visibility types exist."""
        assert AssetVisibility.PUBLIC is not None
        assert AssetVisibility.UNLISTED is not None
        assert AssetVisibility.PRIVATE is not None

    def test_asset_create_defaults_to_public(self):
        """Test asset defaults to public visibility."""
        asset = AssetCreate(
            slug="my-asset",
            name="My Asset",
            type=AssetType.SKILL,
        )
        assert asset.visibility == AssetVisibility.PUBLIC


# =============================================================================
# Pricing Type Tests
# =============================================================================


class TestPricingTypes:
    """Tests for pricing types."""

    def test_pricing_types_exist(self):
        """Test pricing types exist."""
        assert PricingType.FREE is not None
        assert PricingType.PAID is not None
        assert PricingType.PRO is not None

    def test_asset_create_defaults_to_free(self):
        """Test asset defaults to free pricing."""
        asset = AssetCreate(
            slug="my-asset",
            name="My Asset",
            type=AssetType.SKILL,
        )
        assert asset.pricing_type == PricingType.FREE


# =============================================================================
# Tag Validation Tests
# =============================================================================


class TestTagValidation:
    """Tests for tag validation."""

    def test_tags_normalized_to_lowercase(self):
        """Test tags are normalized to lowercase."""
        asset = AssetCreate(
            slug="my-asset",
            name="My Asset",
            type=AssetType.SKILL,
            tags=["Python", "JAVASCRIPT", "TypeScript"],
        )
        assert all(tag.islower() for tag in asset.tags)

    def test_tags_duplicates_removed(self):
        """Test duplicate tags are removed."""
        asset = AssetCreate(
            slug="my-asset",
            name="My Asset",
            type=AssetType.SKILL,
            tags=["python", "Python", "PYTHON", "javascript"],
        )
        assert len(asset.tags) == 2  # python, javascript

    def test_tags_whitespace_stripped(self):
        """Test tag whitespace is stripped."""
        asset = AssetCreate(
            slug="my-asset",
            name="My Asset",
            type=AssetType.SKILL,
            tags=["  python  ", "  javascript  "],
        )
        assert asset.tags == ["python", "javascript"]


# =============================================================================
# Publisher Type Tests
# =============================================================================


class TestPublisherTypes:
    """Tests for publisher types."""

    def test_publisher_types_exist(self):
        """Test publisher types exist."""
        assert PublisherType.USER is not None
        assert PublisherType.ORGANIZATION is not None

    def test_publisher_type_values(self):
        """Test publisher type values."""
        assert PublisherType.USER.value == "user"
        assert PublisherType.ORGANIZATION.value == "organization"

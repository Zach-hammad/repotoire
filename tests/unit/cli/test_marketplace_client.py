"""Unit tests for marketplace API client."""

import pytest
from unittest.mock import MagicMock, patch

from repotoire.cli.marketplace_client import (
    AssetInfo,
    AssetNotFoundError,
    AuthenticationError,
    InstallResult,
    MarketplaceAPIClient,
    MarketplaceAPIError,
    PublisherInfo,
    RateLimitError,
    TierLimitError,
    VersionInfo,
    format_install_count,
    parse_asset_reference,
)


# =============================================================================
# Helper function tests
# =============================================================================


class TestParseAssetReference:
    """Tests for parse_asset_reference function."""

    def test_simple_reference(self):
        """Test parsing simple asset reference."""
        publisher, slug, version = parse_asset_reference("@repotoire/review-pr")
        assert publisher == "repotoire"
        assert slug == "review-pr"
        assert version is None

    def test_reference_with_version(self):
        """Test parsing reference with version."""
        publisher, slug, version = parse_asset_reference("@user/asset@1.2.0")
        assert publisher == "user"
        assert slug == "asset"
        assert version == "1.2.0"

    def test_reference_with_prerelease_version(self):
        """Test parsing reference with prerelease version."""
        publisher, slug, version = parse_asset_reference("@acme/tool@2.0.0-beta.1")
        assert publisher == "acme"
        assert slug == "tool"
        assert version == "2.0.0-beta.1"

    def test_missing_at_symbol_raises(self):
        """Test that missing @ raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            parse_asset_reference("publisher/slug")
        assert "Invalid asset reference" in str(exc_info.value)

    def test_missing_slash_raises(self):
        """Test that missing / raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            parse_asset_reference("@publisherslug")
        assert "Invalid asset reference" in str(exc_info.value)

    def test_empty_publisher_raises(self):
        """Test that empty publisher raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            parse_asset_reference("@/slug")
        assert "Both publisher and slug are required" in str(exc_info.value)

    def test_empty_slug_raises(self):
        """Test that empty slug raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            parse_asset_reference("@publisher/")
        assert "Both publisher and slug are required" in str(exc_info.value)


class TestFormatInstallCount:
    """Tests for format_install_count function."""

    def test_small_number(self):
        """Test formatting small numbers."""
        assert format_install_count(0) == "0"
        assert format_install_count(123) == "123"
        assert format_install_count(999) == "999"

    def test_thousands(self):
        """Test formatting thousands."""
        assert format_install_count(1000) == "1.0k"
        assert format_install_count(1234) == "1.2k"
        assert format_install_count(12500) == "12.5k"
        assert format_install_count(999999) == "1000.0k"

    def test_millions(self):
        """Test formatting millions."""
        assert format_install_count(1000000) == "1.0M"
        assert format_install_count(1234567) == "1.2M"
        assert format_install_count(12500000) == "12.5M"


# =============================================================================
# Data class tests
# =============================================================================


class TestAssetInfo:
    """Tests for AssetInfo dataclass."""

    def test_from_api_response(self):
        """Test creating from API response."""
        data = {
            "id": "asset-123",
            "publisher_slug": "acme",
            "slug": "review-tool",
            "name": "Review Tool",
            "description": "A tool for reviews",
            "asset_type": "command",
            "latest_version": "1.2.0",
            "average_rating": 4.5,
            "install_count": 1234,
            "pricing": "free",
            "is_installed": True,
        }

        asset = AssetInfo.from_api_response(data)

        assert asset.id == "asset-123"
        assert asset.publisher_slug == "acme"
        assert asset.slug == "review-tool"
        assert asset.name == "Review Tool"
        assert asset.description == "A tool for reviews"
        assert asset.asset_type == "command"
        assert asset.latest_version == "1.2.0"
        assert asset.rating == 4.5
        assert asset.install_count == 1234
        assert asset.pricing == "free"
        assert asset.is_installed is True

    def test_from_api_response_nested_publisher(self):
        """Test with nested publisher object."""
        data = {
            "id": "asset-456",
            "publisher": {"slug": "nested-pub"},
            "slug": "my-asset",
            "name": "My Asset",
            "description": "",
            "type": "skill",
        }

        asset = AssetInfo.from_api_response(data)

        assert asset.publisher_slug == "nested-pub"
        assert asset.asset_type == "skill"

    def test_full_name_property(self):
        """Test full_name property."""
        asset = AssetInfo(
            id="123",
            publisher_slug="repotoire",
            slug="review-pr",
            name="Review PR",
            description="",
            asset_type="command",
            latest_version="1.0.0",
            rating=None,
            install_count=0,
            pricing="free",
        )

        assert asset.full_name == "@repotoire/review-pr"


class TestVersionInfo:
    """Tests for VersionInfo dataclass."""

    def test_from_api_response(self):
        """Test creating from API response."""
        data = {
            "version": "1.2.0",
            "changelog": "Bug fixes",
            "published_at": "2024-01-15T10:30:00Z",
            "download_count": 500,
            "checksum": "abc123",
        }

        version = VersionInfo.from_api_response(data)

        assert version.version == "1.2.0"
        assert version.changelog == "Bug fixes"
        assert version.published_at == "2024-01-15T10:30:00Z"
        assert version.download_count == 500
        assert version.checksum == "abc123"

    def test_from_api_response_with_created_at(self):
        """Test with created_at fallback."""
        data = {
            "version": "1.0.0",
            "created_at": "2024-01-01T00:00:00Z",
        }

        version = VersionInfo.from_api_response(data)

        assert version.published_at == "2024-01-01T00:00:00Z"


class TestPublisherInfo:
    """Tests for PublisherInfo dataclass."""

    def test_from_api_response(self):
        """Test creating from API response."""
        data = {
            "id": "pub-123",
            "slug": "acme-corp",
            "display_name": "ACME Corporation",
            "verified": True,
            "asset_count": 10,
        }

        publisher = PublisherInfo.from_api_response(data)

        assert publisher.id == "pub-123"
        assert publisher.slug == "acme-corp"
        assert publisher.display_name == "ACME Corporation"
        assert publisher.verified is True
        assert publisher.asset_count == 10

    def test_from_api_response_with_name_fallback(self):
        """Test with name fallback."""
        data = {
            "id": "pub-456",
            "slug": "user",
            "name": "John Doe",
        }

        publisher = PublisherInfo.from_api_response(data)

        assert publisher.display_name == "John Doe"


# =============================================================================
# API Client tests
# =============================================================================


class TestMarketplaceAPIClient:
    """Tests for MarketplaceAPIClient class."""

    def test_init_with_api_key(self):
        """Test initialization with API key."""
        client = MarketplaceAPIClient(api_key="test-key")
        assert client.api_key == "test-key"

    def test_init_from_env(self):
        """Test initialization from environment variable."""
        with patch.dict("os.environ", {"REPOTOIRE_API_KEY": "env-key"}):
            client = MarketplaceAPIClient()
            assert client.api_key == "env-key"

    def test_init_no_key_raises(self):
        """Test that missing API key raises AuthenticationError."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(AuthenticationError) as exc_info:
                MarketplaceAPIClient()
            assert "REPOTOIRE_API_KEY required" in str(exc_info.value)

    def test_init_custom_base_url(self):
        """Test initialization with custom base URL."""
        client = MarketplaceAPIClient(
            api_key="test",
            base_url="https://custom.api.com/",
        )
        assert client.base_url == "https://custom.api.com"  # Trailing slash stripped

    @patch("httpx.Client")
    def test_search(self, mock_client_class):
        """Test search method."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "assets": [
                {
                    "id": "1",
                    "publisher_slug": "acme",
                    "slug": "tool",
                    "name": "Tool",
                    "description": "A tool",
                    "asset_type": "command",
                    "install_count": 100,
                    "pricing": "free",
                }
            ]
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")
        results = client.search("tool")

        assert len(results) == 1
        assert results[0].slug == "tool"
        mock_client.request.assert_called_once()

    @patch("httpx.Client")
    def test_search_with_filters(self, mock_client_class):
        """Test search with type and category filters."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"assets": []}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")
        client.search("query", asset_type="command", category="productivity")

        call_kwargs = mock_client.request.call_args[1]
        assert call_kwargs["params"]["type"] == "command"
        assert call_kwargs["params"]["category"] == "productivity"

    @patch("httpx.Client")
    def test_get_asset(self, mock_client_class):
        """Test get_asset method."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "1",
            "publisher_slug": "acme",
            "slug": "tool",
            "name": "Tool",
            "description": "A tool",
            "asset_type": "command",
            "install_count": 100,
            "pricing": "free",
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")
        asset = client.get_asset("acme", "tool")

        assert asset.slug == "tool"
        assert asset.publisher_slug == "acme"

    @patch("httpx.Client")
    def test_get_asset_not_found(self, mock_client_class):
        """Test get_asset raises AssetNotFoundError on 404."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")

        with pytest.raises(AssetNotFoundError):
            client.get_asset("unknown", "asset")

    @patch("httpx.Client")
    def test_install(self, mock_client_class):
        """Test install method."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "asset": {
                "id": "1",
                "publisher_slug": "acme",
                "slug": "tool",
                "name": "Tool",
                "description": "A tool",
                "asset_type": "command",
                "install_count": 100,
                "pricing": "free",
            },
            "version": "1.0.0",
            "download_url": "https://example.com/download",
            "checksum": "abc123",
            "dependencies": [],
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")
        result = client.install("acme", "tool")

        assert isinstance(result, InstallResult)
        assert result.version == "1.0.0"
        assert result.download_url == "https://example.com/download"

    @patch("httpx.Client")
    def test_install_tier_limit(self, mock_client_class):
        """Test install raises TierLimitError on 403 with tier message."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"detail": "Tier limit reached"}'
        mock_response.json.return_value = {"detail": "Tier limit reached"}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")

        with pytest.raises(TierLimitError):
            client.install("acme", "tool")

    @patch("httpx.Client")
    def test_rate_limit_error(self, mock_client_class):
        """Test 429 response raises RateLimitError."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "120"}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")

        with pytest.raises(RateLimitError) as exc_info:
            client.search("test")
        assert "120" in str(exc_info.value)

    @patch("httpx.Client")
    def test_auth_error(self, mock_client_class):
        """Test 401 response raises AuthenticationError."""
        mock_response = MagicMock()
        mock_response.status_code = 401

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="invalid")

        with pytest.raises(AuthenticationError):
            client.search("test")

    @patch("httpx.Client")
    def test_uninstall(self, mock_client_class):
        """Test uninstall method."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")
        client.uninstall("acme", "tool")  # Should not raise

    @patch("httpx.Client")
    def test_get_my_publisher(self, mock_client_class):
        """Test get_my_publisher method."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "pub-1",
            "slug": "my-pub",
            "display_name": "My Publisher",
            "verified": False,
            "asset_count": 3,
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")
        publisher = client.get_my_publisher()

        assert publisher is not None
        assert publisher.slug == "my-pub"

    @patch("httpx.Client")
    def test_get_my_publisher_not_found(self, mock_client_class):
        """Test get_my_publisher returns None when not a publisher."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")
        publisher = client.get_my_publisher()

        assert publisher is None

    @patch("httpx.Client")
    def test_validate_asset(self, mock_client_class):
        """Test validate_asset method."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errors": ["Missing prompt field"]}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.request.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")
        errors = client.validate_asset("command", {"description": "No prompt"})

        assert len(errors) == 1
        assert "Missing prompt" in errors[0]

    @patch("httpx.Client")
    def test_download_asset(self, mock_client_class):
        """Test download_asset method."""
        mock_response = MagicMock()
        mock_response.content = b"tarball content"
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_class.return_value = mock_client

        client = MarketplaceAPIClient(api_key="test")
        content = client.download_asset("https://example.com/download")

        assert content == b"tarball content"

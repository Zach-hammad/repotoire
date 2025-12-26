"""Marketplace API client for CLI commands.

This module provides a client for interacting with the Repotoire marketplace API,
handling authentication, requests, and error handling.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from typing import Any

import httpx

from repotoire.logging_config import get_logger
from repotoire import __version__ as CLI_VERSION

logger = get_logger(__name__)

# Default API URL
DEFAULT_API_URL = "https://api.repotoire.com"

# Request timeout in seconds
REQUEST_TIMEOUT = 30.0


class MarketplaceAPIError(Exception):
    """Base exception for marketplace API errors."""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AssetNotFoundError(MarketplaceAPIError):
    """Raised when an asset is not found."""

    pass


class AuthenticationError(MarketplaceAPIError):
    """Raised when authentication fails or API key is missing."""

    pass


class RateLimitError(MarketplaceAPIError):
    """Raised when rate limit is exceeded."""

    pass


class TierLimitError(MarketplaceAPIError):
    """Raised when tier limit is reached."""

    pass


@dataclass
class AssetInfo:
    """Information about a marketplace asset."""

    id: str
    publisher_slug: str
    slug: str
    name: str
    description: str
    asset_type: str
    latest_version: str | None
    rating: float | None
    install_count: int
    pricing: str  # "free", "pro", "paid"
    is_installed: bool = False

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "AssetInfo":
        """Create AssetInfo from API response."""
        return cls(
            id=data.get("id", ""),
            publisher_slug=data.get("publisher_slug", data.get("publisher", {}).get("slug", "")),
            slug=data.get("slug", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            asset_type=data.get("asset_type", data.get("type", "")),
            latest_version=data.get("latest_version"),
            rating=data.get("average_rating"),
            install_count=data.get("install_count", 0),
            pricing=data.get("pricing", "free"),
            is_installed=data.get("is_installed", False),
        )

    @property
    def full_name(self) -> str:
        """Get the full asset name (e.g., @publisher/slug)."""
        return f"@{self.publisher_slug}/{self.slug}"


@dataclass
class VersionInfo:
    """Information about an asset version."""

    version: str
    changelog: str | None
    published_at: str
    download_count: int
    checksum: str | None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "VersionInfo":
        """Create VersionInfo from API response."""
        return cls(
            version=data.get("version", ""),
            changelog=data.get("changelog"),
            published_at=data.get("published_at", data.get("created_at", "")),
            download_count=data.get("download_count", 0),
            checksum=data.get("checksum"),
        )


@dataclass
class InstallResult:
    """Result of installing an asset."""

    asset: AssetInfo
    version: str
    download_url: str
    checksum: str
    dependencies: list[str]

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "InstallResult":
        """Create InstallResult from API response."""
        asset_data = data.get("asset", data)
        return cls(
            asset=AssetInfo.from_api_response(asset_data),
            version=data.get("version", data.get("installed_version", "")),
            download_url=data.get("download_url", ""),
            checksum=data.get("checksum", ""),
            dependencies=data.get("dependencies", []),
        )


@dataclass
class PublisherInfo:
    """Information about a marketplace publisher."""

    id: str
    slug: str
    display_name: str
    verified: bool
    asset_count: int

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "PublisherInfo":
        """Create PublisherInfo from API response."""
        return cls(
            id=data.get("id", ""),
            slug=data.get("slug", ""),
            display_name=data.get("display_name", data.get("name", "")),
            verified=data.get("verified", False),
            asset_count=data.get("asset_count", 0),
        )


class MarketplaceAPIClient:
    """Client for interacting with the Repotoire marketplace API."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        """Initialize the API client.

        Args:
            api_key: API key for authentication. If not provided, uses REPOTOIRE_API_KEY env var.
            base_url: Base URL for the API. If not provided, uses REPOTOIRE_API_URL env var.
        """
        self.api_key = api_key or os.environ.get("REPOTOIRE_API_KEY")
        self.base_url = (base_url or os.environ.get("REPOTOIRE_API_URL", DEFAULT_API_URL)).rstrip("/")

        if not self.api_key:
            raise AuthenticationError(
                "REPOTOIRE_API_KEY required.\n"
                "Get your key at: https://repotoire.com/settings/api-keys"
            )

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-CLI-Version": CLI_VERSION,
            "X-Platform": f"{platform.system()}/{platform.release()}",
        }

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """Handle API response and raise appropriate errors.

        Args:
            response: HTTP response from the API.

        Returns:
            Parsed JSON response.

        Raises:
            MarketplaceAPIError: If the request failed.
        """
        if response.status_code == 401:
            raise AuthenticationError(
                "Invalid API key or unauthorized.\n"
                "Check your key at: https://repotoire.com/settings/api-keys",
                status_code=401,
            )

        if response.status_code == 404:
            raise AssetNotFoundError("Asset not found", status_code=404)

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "60")
            raise RateLimitError(
                f"Rate limit exceeded. Try again in {retry_after} seconds.",
                status_code=429,
            )

        if response.status_code == 403:
            data = response.json() if response.text else {}
            message = data.get("detail", "Access denied")
            if "tier" in message.lower() or "limit" in message.lower():
                raise TierLimitError(message, status_code=403)
            raise MarketplaceAPIError(message, status_code=403)

        if response.status_code >= 400:
            try:
                data = response.json()
                message = data.get("detail", data.get("message", str(data)))
            except Exception:
                message = response.text or f"HTTP {response.status_code}"
            raise MarketplaceAPIError(message, status_code=response.status_code)

        try:
            return response.json()
        except Exception:
            return {}

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an API request.

        Args:
            method: HTTP method (GET, POST, DELETE, etc.).
            path: API path (e.g., "/api/v1/marketplace/search").
            params: Query parameters.
            json_data: JSON body data.

        Returns:
            Parsed JSON response.
        """
        url = f"{self.base_url}{path}"

        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.request(
                method=method,
                url=url,
                headers=self._get_headers(),
                params=params,
                json=json_data,
            )
            return self._handle_response(response)

    # =========================================================================
    # Search and Browse
    # =========================================================================

    def search(
        self,
        query: str,
        asset_type: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[AssetInfo]:
        """Search for marketplace assets.

        Args:
            query: Search query text.
            asset_type: Filter by asset type (command, skill, style, hook, prompt).
            category: Filter by category.
            limit: Maximum number of results.
            offset: Offset for pagination.

        Returns:
            List of matching assets.
        """
        params: dict[str, Any] = {
            "q": query,
            "limit": limit,
            "offset": offset,
        }

        if asset_type:
            params["type"] = asset_type
        if category:
            params["category"] = category

        data = self._request("GET", "/api/v1/marketplace/search", params=params)
        assets = data.get("assets", data.get("results", data if isinstance(data, list) else []))

        return [AssetInfo.from_api_response(a) for a in assets]

    def browse(
        self,
        sort: str = "popular",
        asset_type: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[AssetInfo]:
        """Browse marketplace assets.

        Args:
            sort: Sort order (popular, recent, rating, trending).
            asset_type: Filter by asset type.
            category: Filter by category.
            limit: Maximum number of results.
            offset: Offset for pagination.

        Returns:
            List of assets.
        """
        params: dict[str, Any] = {
            "sort": sort,
            "limit": limit,
            "offset": offset,
        }

        if asset_type:
            params["type"] = asset_type
        if category:
            params["category"] = category

        data = self._request("GET", "/api/v1/marketplace/browse", params=params)
        assets = data.get("assets", data.get("results", data if isinstance(data, list) else []))

        return [AssetInfo.from_api_response(a) for a in assets]

    def get_asset(self, publisher: str, slug: str) -> AssetInfo:
        """Get details of a specific asset.

        Args:
            publisher: Publisher's slug.
            slug: Asset's slug.

        Returns:
            Asset information.

        Raises:
            AssetNotFoundError: If the asset doesn't exist.
        """
        data = self._request("GET", f"/api/v1/marketplace/@{publisher}/{slug}")
        return AssetInfo.from_api_response(data)

    def get_asset_versions(
        self,
        publisher: str,
        slug: str,
        limit: int = 10,
    ) -> list[VersionInfo]:
        """Get all versions of an asset.

        Args:
            publisher: Publisher's slug.
            slug: Asset's slug.
            limit: Maximum number of versions to return.

        Returns:
            List of versions.
        """
        data = self._request(
            "GET",
            f"/api/v1/marketplace/@{publisher}/{slug}/versions",
            params={"limit": limit},
        )
        versions = data.get("versions", data if isinstance(data, list) else [])

        return [VersionInfo.from_api_response(v) for v in versions]

    # =========================================================================
    # Install and Uninstall
    # =========================================================================

    def install(
        self,
        publisher: str,
        slug: str,
        version: str | None = None,
        org_id: str | None = None,
    ) -> InstallResult:
        """Install an asset.

        Args:
            publisher: Publisher's slug.
            slug: Asset's slug.
            version: Specific version to install (default: latest).
            org_id: Organization ID (for team installs).

        Returns:
            Installation result with download URL.

        Raises:
            AssetNotFoundError: If the asset doesn't exist.
            TierLimitError: If tier limit is reached.
        """
        json_data: dict[str, Any] = {}
        if version:
            json_data["version"] = version
        if org_id:
            json_data["org_id"] = org_id

        data = self._request(
            "POST",
            f"/api/v1/marketplace/@{publisher}/{slug}/install",
            json_data=json_data if json_data else None,
        )

        return InstallResult.from_api_response(data)

    def uninstall(
        self,
        publisher: str,
        slug: str,
        org_id: str | None = None,
    ) -> None:
        """Uninstall an asset.

        Args:
            publisher: Publisher's slug.
            slug: Asset's slug.
            org_id: Organization ID (for team uninstalls).
        """
        json_data: dict[str, Any] = {}
        if org_id:
            json_data["org_id"] = org_id

        self._request(
            "DELETE",
            f"/api/v1/marketplace/@{publisher}/{slug}/install",
            json_data=json_data if json_data else None,
        )

    def get_installed(self, org_id: str | None = None) -> list[AssetInfo]:
        """Get list of installed assets.

        Args:
            org_id: Organization ID to get team installs.

        Returns:
            List of installed assets.
        """
        params: dict[str, Any] = {}
        if org_id:
            params["org_id"] = org_id

        data = self._request("GET", "/api/v1/marketplace/installed", params=params)
        assets = data.get("assets", data.get("installed", data if isinstance(data, list) else []))

        return [AssetInfo.from_api_response(a) for a in assets]

    # =========================================================================
    # Sync
    # =========================================================================

    def sync(self, since: str | None = None) -> dict[str, Any]:
        """Sync installed assets, returning updates.

        Args:
            since: ISO timestamp to get changes since (for incremental sync).

        Returns:
            Sync result with assets to update/download.
        """
        params: dict[str, Any] = {}
        if since:
            params["since"] = since

        return self._request("GET", "/api/v1/marketplace/sync", params=params)

    def download_asset(self, download_url: str) -> bytes:
        """Download asset content from presigned URL.

        Args:
            download_url: Presigned URL to download from.

        Returns:
            Raw tarball bytes.
        """
        with httpx.Client(timeout=60.0) as client:
            response = client.get(download_url)
            response.raise_for_status()
            return response.content

    # =========================================================================
    # Analytics
    # =========================================================================

    def track_event(
        self,
        publisher: str,
        slug: str,
        event_type: str,
        version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Track an analytics event for an asset.

        This is a fire-and-forget operation - errors are logged but not raised.

        Args:
            publisher: Publisher's slug.
            slug: Asset's slug.
            event_type: Event type (download, install, uninstall, update).
            version: Version being installed/updated to.
            metadata: Optional additional metadata.
        """
        try:
            json_data: dict[str, Any] = {
                "event_type": event_type,
            }
            if version:
                json_data["version"] = version
            if metadata:
                json_data["metadata"] = metadata

            # Add CLI-specific metadata
            json_data.setdefault("metadata", {})
            json_data["metadata"]["cli_version"] = CLI_VERSION
            json_data["metadata"]["platform"] = f"{platform.system()}/{platform.release()}"

            self._request(
                "POST",
                f"/api/v1/marketplace/analytics/events/@{publisher}/{slug}",
                json_data=json_data,
            )
        except Exception as e:
            # Log but don't fail the operation
            logger.debug(f"Failed to track event: {e}")

    # =========================================================================
    # Publishing
    # =========================================================================

    def validate_asset(self, asset_type: str, content: dict[str, Any]) -> list[str]:
        """Validate asset content before publishing.

        Args:
            asset_type: Type of asset (command, skill, style, hook, prompt).
            content: Asset content dictionary.

        Returns:
            List of validation errors (empty if valid).
        """
        data = self._request(
            "POST",
            "/api/v1/marketplace/validate",
            json_data={
                "type": asset_type,
                "content": content,
            },
        )

        return data.get("errors", [])

    def get_my_publisher(self) -> PublisherInfo | None:
        """Get the current user's publisher profile.

        Returns:
            Publisher info or None if not a publisher.
        """
        try:
            data = self._request("GET", "/api/v1/marketplace/publisher/me")
            return PublisherInfo.from_api_response(data)
        except AssetNotFoundError:
            return None

    def create_publisher(self, slug: str, display_name: str) -> PublisherInfo:
        """Create a new publisher profile.

        Args:
            slug: Publisher slug (must be unique).
            display_name: Display name for the publisher.

        Returns:
            Created publisher info.
        """
        data = self._request(
            "POST",
            "/api/v1/marketplace/publisher",
            json_data={
                "slug": slug,
                "display_name": display_name,
            },
        )

        return PublisherInfo.from_api_response(data)

    def publish_version(
        self,
        publisher: str,
        slug: str,
        version: str,
        content: dict[str, Any],
        changelog: str | None = None,
        asset_type: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Publish a new version of an asset.

        Args:
            publisher: Publisher's slug.
            slug: Asset's slug.
            version: Semantic version string.
            content: Asset content dictionary.
            changelog: Changelog for this version.
            asset_type: Asset type (required for new assets).
            name: Asset name (required for new assets).
            description: Asset description (required for new assets).

        Returns:
            Published version details.

        Raises:
            TierLimitError: If publishing limit reached.
        """
        json_data: dict[str, Any] = {
            "version": version,
            "content": content,
        }

        if changelog:
            json_data["changelog"] = changelog
        if asset_type:
            json_data["asset_type"] = asset_type
        if name:
            json_data["name"] = name
        if description:
            json_data["description"] = description

        return self._request(
            "POST",
            f"/api/v1/marketplace/@{publisher}/{slug}/versions",
            json_data=json_data,
        )


def parse_asset_reference(reference: str) -> tuple[str, str, str | None]:
    """Parse an asset reference string.

    Args:
        reference: Asset reference (e.g., "@publisher/slug" or "@publisher/slug@1.0.0").

    Returns:
        Tuple of (publisher, slug, version).

    Raises:
        ValueError: If the reference format is invalid.

    Examples:
        >>> parse_asset_reference("@repotoire/review-pr")
        ("repotoire", "review-pr", None)
        >>> parse_asset_reference("@user/asset@1.2.0")
        ("user", "asset", "1.2.0")
    """
    if not reference.startswith("@"):
        raise ValueError(
            f"Invalid asset reference: {reference}\n"
            "Format should be: @publisher/slug or @publisher/slug@version"
        )

    # Remove leading @
    reference = reference[1:]

    # Check for version
    version = None
    if "@" in reference:
        ref_parts = reference.rsplit("@", 1)
        reference = ref_parts[0]
        version = ref_parts[1]

    # Split publisher and slug
    if "/" not in reference:
        raise ValueError(
            f"Invalid asset reference: @{reference}\n"
            "Format should be: @publisher/slug or @publisher/slug@version"
        )

    parts = reference.split("/", 1)
    publisher = parts[0]
    slug = parts[1]

    if not publisher or not slug:
        raise ValueError(
            f"Invalid asset reference: @{reference}\n"
            "Both publisher and slug are required"
        )

    return publisher, slug, version


def format_install_count(count: int) -> str:
    """Format large numbers for display.

    Args:
        count: Number to format.

    Returns:
        Formatted string (e.g., "1.2k", "1.2M").

    Examples:
        >>> format_install_count(1234)
        "1.2k"
        >>> format_install_count(1234567)
        "1.2M"
    """
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}k"
    else:
        return str(count)

"""API v1 schemas (Pydantic models)."""

from repotoire.api.v1.schemas.marketplace import (
    AssetCreate,
    AssetDetail,
    AssetListItem,
    AssetSearchParams,
    AssetUpdate,
    CategoriesResponse,
    CategoryInfo,
    InstallRequest,
    InstallResponse,
    InstallUpdateRequest,
    InstallWithContent,
    OrgAssetCreate,
    OrgAssetResponse,
    OrgAssetUpdate,
    PaginatedResponse,
    PublisherCreate,
    PublisherResponse,
    PublisherUpdate,
    RatingSummary,
    ReviewCreate,
    ReviewListResponse,
    ReviewResponse,
    ReviewUpdate,
    SyncResponse,
    VersionCreate,
    VersionDetailResponse,
    VersionResponse,
)

__all__ = [
    # Pagination
    "PaginatedResponse",
    # Publisher
    "PublisherCreate",
    "PublisherUpdate",
    "PublisherResponse",
    # Asset
    "AssetCreate",
    "AssetUpdate",
    "AssetListItem",
    "AssetDetail",
    "AssetSearchParams",
    # Version
    "VersionCreate",
    "VersionResponse",
    "VersionDetailResponse",
    # Install
    "InstallRequest",
    "InstallResponse",
    "InstallUpdateRequest",
    "InstallWithContent",
    "SyncResponse",
    # Review
    "ReviewCreate",
    "ReviewUpdate",
    "ReviewResponse",
    "ReviewListResponse",
    "RatingSummary",
    # Org Private
    "OrgAssetCreate",
    "OrgAssetUpdate",
    "OrgAssetResponse",
    # Categories
    "CategoryInfo",
    "CategoriesResponse",
]

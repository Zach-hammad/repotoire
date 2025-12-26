"""Asset storage service for marketplace.

This module provides S3/R2-compatible storage for marketplace assets with:
- Version storage and retrieval
- Presigned URLs for secure downloads
- Icon uploads with caching
- Content integrity via SHA-256
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from hashlib import sha256
from typing import Optional

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Configuration from environment
R2_ENDPOINT_URL = os.environ.get("R2_ENDPOINT_URL")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("R2_BUCKET", "repotoire-marketplace")
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")

# Default presigned URL expiration: 1 hour
DEFAULT_PRESIGNED_EXPIRATION = 3600

# Icon cache control: 1 year
ICON_CACHE_CONTROL = "public, max-age=31536000, immutable"

# Thread pool for async execution of boto3 sync calls
_executor = ThreadPoolExecutor(max_workers=4)


@dataclass
class UploadResult:
    """Result of an upload operation."""

    url: str  # S3/R2 key (not presigned)
    checksum: str  # SHA-256 hex digest
    size: int  # Size in bytes


class StorageError(Exception):
    """Base exception for storage errors."""

    pass


class StorageNotConfiguredError(StorageError):
    """Raised when storage is not properly configured."""

    pass


class AssetNotFoundError(StorageError):
    """Raised when an asset is not found."""

    pass


def _get_s3_client():
    """Get configured S3/R2 client.

    Returns:
        boto3 S3 client configured for R2 or compatible storage.

    Raises:
        StorageNotConfiguredError: If storage is not configured.
    """
    try:
        import boto3
    except ImportError:
        raise StorageError(
            "boto3 is required for asset storage. Install with: pip install boto3"
        )

    if not is_storage_configured():
        raise StorageNotConfiguredError(
            "R2 storage not configured. Set R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY, and R2_BUCKET environment variables."
        )

    # Determine endpoint URL
    endpoint_url = R2_ENDPOINT_URL
    if not endpoint_url and R2_ACCOUNT_ID:
        endpoint_url = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def is_storage_configured() -> bool:
    """Check if storage is properly configured.

    Returns:
        True if all required environment variables are set.
    """
    return bool(
        (R2_ENDPOINT_URL or R2_ACCOUNT_ID)
        and R2_ACCESS_KEY_ID
        and R2_SECRET_ACCESS_KEY
        and R2_BUCKET
    )


def _get_version_key(publisher_slug: str, asset_slug: str, version: str) -> str:
    """Get the S3 key for an asset version.

    Path convention: assets/@{publisher_slug}/{asset_slug}/{version}.tar.gz
    """
    return f"assets/@{publisher_slug}/{asset_slug}/{version}.tar.gz"


def _get_icon_key(asset_id: str) -> str:
    """Get the S3 key for an asset icon.

    Path convention: icons/{asset_id}.png
    """
    return f"icons/{asset_id}.png"


class AssetStorageService:
    """Service for storing and retrieving marketplace assets."""

    def __init__(self):
        """Initialize the storage service."""
        self._client = None

    @property
    def client(self):
        """Get or create the S3 client."""
        if self._client is None:
            self._client = _get_s3_client()
        return self._client

    async def upload_version(
        self,
        publisher_slug: str,
        asset_slug: str,
        version: str,
        content: bytes,
        content_type: str = "application/gzip",
    ) -> UploadResult:
        """Upload an asset version to storage.

        Args:
            publisher_slug: Publisher's URL slug.
            asset_slug: Asset's URL slug.
            version: Semantic version string.
            content: Gzipped tarball content.
            content_type: MIME type of the content.

        Returns:
            UploadResult with URL, checksum, and size.

        Raises:
            StorageError: If upload fails.
        """
        key = _get_version_key(publisher_slug, asset_slug, version)
        checksum = sha256(content).hexdigest()
        size = len(content)

        def _upload():
            self.client.put_object(
                Bucket=R2_BUCKET,
                Key=key,
                Body=content,
                ContentType=content_type,
                Metadata={
                    "publisher": publisher_slug,
                    "asset": asset_slug,
                    "version": version,
                    "checksum": checksum,
                },
            )
            return key

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, _upload)

            logger.info(
                f"Uploaded asset version: {key} "
                f"(size={size}, checksum={checksum[:8]}...)"
            )

            return UploadResult(url=key, checksum=checksum, size=size)

        except Exception as e:
            logger.error(f"Failed to upload version {key}: {e}")
            raise StorageError(f"Upload failed: {e}") from e

    async def download_version(
        self,
        publisher_slug: str,
        asset_slug: str,
        version: str,
    ) -> bytes:
        """Download an asset version from storage.

        Args:
            publisher_slug: Publisher's URL slug.
            asset_slug: Asset's URL slug.
            version: Semantic version string.

        Returns:
            Raw bytes of the gzipped tarball.

        Raises:
            AssetNotFoundError: If the version doesn't exist.
            StorageError: If download fails.
        """
        key = _get_version_key(publisher_slug, asset_slug, version)

        def _download():
            response = self.client.get_object(Bucket=R2_BUCKET, Key=key)
            return response["Body"].read()

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(_executor, _download)

            logger.info(f"Downloaded asset version: {key}")
            return data

        except Exception as e:
            # Check for NoSuchKey error (from boto3 or string representation)
            error_str = str(e)
            if "NoSuchKey" in error_str or "404" in error_str or "Not Found" in error_str:
                raise AssetNotFoundError(
                    f"Asset version not found: {publisher_slug}/{asset_slug}@{version}"
                )
            logger.error(f"Failed to download version {key}: {e}")
            raise StorageError(f"Download failed: {e}") from e

    async def get_presigned_url(
        self,
        publisher_slug: str,
        asset_slug: str,
        version: str,
        expires_in: int = DEFAULT_PRESIGNED_EXPIRATION,
    ) -> str:
        """Generate a presigned URL for downloading an asset version.

        Args:
            publisher_slug: Publisher's URL slug.
            asset_slug: Asset's URL slug.
            version: Semantic version string.
            expires_in: URL expiration time in seconds (default: 1 hour).

        Returns:
            Presigned download URL.

        Raises:
            StorageError: If URL generation fails.
        """
        key = _get_version_key(publisher_slug, asset_slug, version)

        def _generate_url():
            return self.client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": R2_BUCKET,
                    "Key": key,
                },
                ExpiresIn=expires_in,
            )

        try:
            loop = asyncio.get_event_loop()
            url = await loop.run_in_executor(_executor, _generate_url)

            logger.info(f"Generated presigned URL for {key} (expires in {expires_in}s)")
            return url

        except Exception as e:
            logger.error(f"Failed to generate presigned URL for {key}: {e}")
            raise StorageError(f"Presigned URL generation failed: {e}") from e

    async def delete_version(
        self,
        publisher_slug: str,
        asset_slug: str,
        version: str,
    ) -> None:
        """Delete an asset version from storage.

        Args:
            publisher_slug: Publisher's URL slug.
            asset_slug: Asset's URL slug.
            version: Semantic version string.

        Raises:
            StorageError: If deletion fails.
        """
        key = _get_version_key(publisher_slug, asset_slug, version)

        def _delete():
            self.client.delete_object(Bucket=R2_BUCKET, Key=key)

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, _delete)

            logger.info(f"Deleted asset version: {key}")

        except Exception as e:
            logger.error(f"Failed to delete version {key}: {e}")
            raise StorageError(f"Delete failed: {e}") from e

    async def upload_icon(
        self,
        asset_id: str,
        image_bytes: bytes,
        content_type: str = "image/png",
    ) -> str:
        """Upload an asset icon to storage.

        Args:
            asset_id: UUID of the asset.
            image_bytes: Raw image data.
            content_type: MIME type of the image.

        Returns:
            Public URL of the uploaded icon.

        Raises:
            StorageError: If upload fails.
        """
        key = _get_icon_key(asset_id)

        def _upload():
            self.client.put_object(
                Bucket=R2_BUCKET,
                Key=key,
                Body=image_bytes,
                ContentType=content_type,
                CacheControl=ICON_CACHE_CONTROL,
            )
            return key

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, _upload)

            logger.info(f"Uploaded icon: {key}")

            # Return the key (caller can construct public URL if needed)
            return key

        except Exception as e:
            logger.error(f"Failed to upload icon {key}: {e}")
            raise StorageError(f"Icon upload failed: {e}") from e

    async def delete_icon(self, asset_id: str) -> None:
        """Delete an asset icon from storage.

        Args:
            asset_id: UUID of the asset.

        Raises:
            StorageError: If deletion fails.
        """
        key = _get_icon_key(asset_id)

        def _delete():
            self.client.delete_object(Bucket=R2_BUCKET, Key=key)

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, _delete)

            logger.info(f"Deleted icon: {key}")

        except Exception as e:
            logger.error(f"Failed to delete icon {key}: {e}")
            raise StorageError(f"Icon delete failed: {e}") from e

    async def version_exists(
        self,
        publisher_slug: str,
        asset_slug: str,
        version: str,
    ) -> bool:
        """Check if an asset version exists in storage.

        Args:
            publisher_slug: Publisher's URL slug.
            asset_slug: Asset's URL slug.
            version: Semantic version string.

        Returns:
            True if the version exists, False otherwise.
        """
        key = _get_version_key(publisher_slug, asset_slug, version)

        def _head():
            try:
                self.client.head_object(Bucket=R2_BUCKET, Key=key)
                return True
            except Exception:
                return False

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(_executor, _head)

        except Exception:
            return False

    async def get_version_metadata(
        self,
        publisher_slug: str,
        asset_slug: str,
        version: str,
    ) -> dict:
        """Get metadata for an asset version.

        Args:
            publisher_slug: Publisher's URL slug.
            asset_slug: Asset's URL slug.
            version: Semantic version string.

        Returns:
            Dictionary with size, checksum, and other metadata.

        Raises:
            AssetNotFoundError: If the version doesn't exist.
            StorageError: If retrieval fails.
        """
        key = _get_version_key(publisher_slug, asset_slug, version)

        def _head():
            response = self.client.head_object(Bucket=R2_BUCKET, Key=key)
            return {
                "size": response.get("ContentLength", 0),
                "content_type": response.get("ContentType", ""),
                "last_modified": response.get("LastModified"),
                "checksum": response.get("Metadata", {}).get("checksum", ""),
            }

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(_executor, _head)

        except Exception as e:
            if "NoSuchKey" in str(e) or "404" in str(e):
                raise AssetNotFoundError(
                    f"Asset version not found: {publisher_slug}/{asset_slug}@{version}"
                )
            logger.error(f"Failed to get metadata for {key}: {e}")
            raise StorageError(f"Metadata retrieval failed: {e}") from e

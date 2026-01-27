"""Cloud storage service for data exports.

This module provides S3-compatible storage for data exports with presigned URLs.
Supports AWS S3, Cloudflare R2, and other S3-compatible providers.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from repotoire.api.shared.services.s3_client import get_default_config, get_s3_client
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Configuration from environment
S3_BUCKET_NAME = os.environ.get("EXPORTS_BUCKET_NAME", "repotoire-exports")

# Presigned URL expiration (default: 7 days)
PRESIGNED_URL_EXPIRATION = int(os.environ.get("PRESIGNED_URL_EXPIRATION_SECONDS", 7 * 24 * 60 * 60))


def _get_s3_client():
    """Get configured S3 client.

    Returns:
        boto3 S3 client configured for the storage provider.
    """
    return get_s3_client(get_default_config())


async def upload_export(
    content: str,
    export_id: str,
    content_type: str = "application/json",
) -> str:
    """Upload export data to cloud storage.

    Args:
        content: The export content as a string.
        export_id: Unique identifier for the export.
        content_type: MIME type of the content.

    Returns:
        The S3 key where the content was uploaded.

    Raises:
        Exception: If upload fails.
    """
    key = f"exports/{export_id}.json"

    try:
        s3 = _get_s3_client()

        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=content.encode("utf-8"),
            ContentType=content_type,
            Metadata={
                "export-id": export_id,
                "created-at": datetime.now(timezone.utc).isoformat(),
            },
        )

        logger.info(f"Uploaded export to {S3_BUCKET_NAME}/{key}")
        return key

    except Exception as e:
        logger.error(f"Failed to upload export {export_id}: {e}")
        raise


async def generate_presigned_url(
    key: str,
    expiration_seconds: Optional[int] = None,
) -> str:
    """Generate a presigned URL for downloading an export.

    Args:
        key: The S3 key of the object.
        expiration_seconds: URL expiration time in seconds (default: 7 days).

    Returns:
        Presigned URL for downloading the export.

    Raises:
        Exception: If URL generation fails.
    """
    if expiration_seconds is None:
        expiration_seconds = PRESIGNED_URL_EXPIRATION

    try:
        s3 = _get_s3_client()

        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": S3_BUCKET_NAME,
                "Key": key,
            },
            ExpiresIn=expiration_seconds,
        )

        logger.info(f"Generated presigned URL for {key} (expires in {expiration_seconds}s)")
        return url

    except Exception as e:
        logger.error(f"Failed to generate presigned URL for {key}: {e}")
        raise


async def upload_export_with_url(
    content: str,
    export_id: str,
    content_type: str = "application/json",
    url_expiration_seconds: Optional[int] = None,
) -> str:
    """Upload export and return a presigned download URL.

    This is a convenience function that combines upload_export and
    generate_presigned_url.

    Args:
        content: The export content as a string.
        export_id: Unique identifier for the export.
        content_type: MIME type of the content.
        url_expiration_seconds: URL expiration time in seconds.

    Returns:
        Presigned URL for downloading the export.
    """
    key = await upload_export(content, export_id, content_type)
    return await generate_presigned_url(key, url_expiration_seconds)


async def delete_export(export_id: str) -> bool:
    """Delete an export from cloud storage.

    Args:
        export_id: Unique identifier for the export.

    Returns:
        True if deleted successfully, False otherwise.
    """
    key = f"exports/{export_id}.json"

    try:
        s3 = _get_s3_client()
        s3.delete_object(Bucket=S3_BUCKET_NAME, Key=key)
        logger.info(f"Deleted export {key}")
        return True

    except Exception as e:
        logger.error(f"Failed to delete export {export_id}: {e}")
        return False


def is_storage_configured() -> bool:
    """Check if cloud storage is properly configured.

    Returns:
        True if storage credentials are available.
    """
    config = get_default_config()
    return config.is_configured()

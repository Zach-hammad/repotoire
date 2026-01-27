"""Shared S3/R2 client factory.

Provides thread-safe singleton S3 client creation for different storage providers.
Supports AWS S3, Cloudflare R2, and S3-compatible endpoints.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = get_logger(__name__)


class StorageProvider(str, Enum):
    """Supported storage providers."""

    AWS_S3 = "s3"
    CLOUDFLARE_R2 = "r2"
    CUSTOM = "custom"


@dataclass
class S3Config:
    """Configuration for S3-compatible storage.

    Attributes:
        provider: Storage provider type.
        endpoint_url: Custom endpoint URL (for R2 or custom S3-compatible storage).
        access_key_id: AWS/R2 access key ID.
        secret_access_key: AWS/R2 secret access key.
        region: AWS region or "auto" for R2.
        account_id: Cloudflare account ID (for R2 endpoint generation).
    """

    provider: StorageProvider = StorageProvider.AWS_S3
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None
    region: str = "us-east-1"
    account_id: str | None = None

    def __post_init__(self) -> None:
        """Validate and normalize configuration."""
        # Generate R2 endpoint if not provided
        if (
            self.provider == StorageProvider.CLOUDFLARE_R2
            and not self.endpoint_url
            and self.account_id
        ):
            self.endpoint_url = f"https://{self.account_id}.r2.cloudflarestorage.com"
            self.region = "auto"

    def is_configured(self) -> bool:
        """Check if configuration has required credentials."""
        return bool(self.access_key_id and self.secret_access_key)


@dataclass
class S3ClientCache:
    """Thread-safe cache for S3 clients.

    Each unique configuration gets its own cached client.
    """

    _clients: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _config_key(self, config: S3Config) -> str:
        """Generate a unique key for a configuration."""
        return f"{config.provider}:{config.endpoint_url}:{config.region}"

    def get_or_create(self, config: S3Config) -> "S3Client":
        """Get or create an S3 client for the given configuration.

        Thread-safe via double-checked locking pattern.

        Args:
            config: S3 configuration.

        Returns:
            boto3 S3 client.

        Raises:
            ImportError: If boto3 is not installed.
            ValueError: If configuration is invalid.
        """
        key = self._config_key(config)

        # Fast path: return existing client without lock
        if key in self._clients:
            return self._clients[key]

        # Slow path: acquire lock and check again
        with self._lock:
            if key not in self._clients:
                self._clients[key] = _create_s3_client(config)
                logger.debug(f"Created S3 client for {config.provider.value}")

        return self._clients[key]

    def clear(self) -> None:
        """Clear all cached clients."""
        with self._lock:
            self._clients.clear()
            logger.debug("Cleared S3 client cache")


# Global client cache
_client_cache = S3ClientCache()


def _create_s3_client(config: S3Config) -> "S3Client":
    """Create a new S3 client for the given configuration.

    Args:
        config: S3 configuration.

    Returns:
        boto3 S3 client.

    Raises:
        ImportError: If boto3 is not installed.
        ValueError: If configuration is invalid.
    """
    try:
        import boto3
    except ImportError:
        raise ImportError(
            "boto3 is required for S3 storage. Install with: pip install boto3"
        )

    if not config.is_configured():
        raise ValueError(
            "S3 client requires access_key_id and secret_access_key. "
            "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY or "
            "R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY environment variables."
        )

    client_kwargs: dict[str, Any] = {
        "service_name": "s3",
        "aws_access_key_id": config.access_key_id,
        "aws_secret_access_key": config.secret_access_key,
        "region_name": config.region,
    }

    if config.endpoint_url:
        client_kwargs["endpoint_url"] = config.endpoint_url

    return boto3.client(**client_kwargs)


def get_s3_client(config: S3Config | None = None) -> "S3Client":
    """Get a thread-safe singleton S3 client.

    Args:
        config: S3 configuration. If None, uses default from environment.

    Returns:
        boto3 S3 client.

    Raises:
        ImportError: If boto3 is not installed.
        ValueError: If configuration is invalid.
    """
    if config is None:
        config = get_default_config()

    return _client_cache.get_or_create(config)


def get_default_config() -> S3Config:
    """Get default S3 configuration from environment variables.

    Supports both AWS S3 and Cloudflare R2 via environment variables.

    Returns:
        S3Config populated from environment.
    """
    provider_str = os.environ.get("STORAGE_PROVIDER", "s3").lower()

    if provider_str == "r2":
        return S3Config(
            provider=StorageProvider.CLOUDFLARE_R2,
            endpoint_url=os.environ.get("R2_ENDPOINT_URL"),
            access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
            account_id=os.environ.get("R2_ACCOUNT_ID"),
            region="auto",
        )
    else:
        endpoint_url = os.environ.get("S3_ENDPOINT_URL")
        provider = (
            StorageProvider.CUSTOM if endpoint_url else StorageProvider.AWS_S3
        )

        return S3Config(
            provider=provider,
            endpoint_url=endpoint_url,
            access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region=os.environ.get("AWS_REGION", "us-east-1"),
        )


def get_r2_config() -> S3Config:
    """Get Cloudflare R2 configuration from environment variables.

    Convenience function for R2-specific usage.

    Returns:
        S3Config configured for R2.
    """
    return S3Config(
        provider=StorageProvider.CLOUDFLARE_R2,
        endpoint_url=os.environ.get("R2_ENDPOINT_URL"),
        access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
        secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
        account_id=os.environ.get("R2_ACCOUNT_ID"),
        region="auto",
    )


def clear_client_cache() -> None:
    """Clear the global S3 client cache.

    Useful for testing or when configuration changes.
    """
    _client_cache.clear()

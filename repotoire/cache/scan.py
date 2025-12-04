"""Content-hash based cache for secrets scan results.

Scan results are cached using MD5 hash of content as key, which provides:
- Automatic invalidation when file content changes
- Long TTL (24 hours) since unchanged files have identical scan results
- Efficient reuse across multiple analysis runs
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Dict, List, Optional

from pydantic import BaseModel, Field

from repotoire.cache.base import BaseCache
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

# Default TTL: 24 hours
DEFAULT_SCAN_TTL_SECONDS = 86400


class CachedSecretMatch(BaseModel):
    """Cached representation of a secret match.

    Subset of SecretMatch fields needed for caching.
    """

    secret_type: str = Field(..., description="Type of secret detected")
    line_number: int = Field(..., description="Line number where found")
    risk_level: str = Field(..., description="Risk level: critical, high, medium, low")
    remediation: str = Field(default="", description="Remediation suggestion")


class CachedScanResult(BaseModel):
    """Cached representation of secrets scan result.

    Minimal data needed to avoid re-scanning:
    - Whether secrets were found
    - Count and types of secrets
    - Basic match info for reporting

    Note: Does not cache redacted_text as it's large and
    can be regenerated quickly if needed.
    """

    has_secrets: bool = Field(..., description="Whether secrets were found")
    total_secrets: int = Field(..., description="Count of detected secrets")
    by_risk_level: Dict[str, int] = Field(
        default_factory=dict,
        description="Count by risk level",
    )
    by_type: Dict[str, int] = Field(
        default_factory=dict,
        description="Count by secret type",
    )
    matches: List[CachedSecretMatch] = Field(
        default_factory=list,
        description="Detected secrets (limited info)",
    )
    file_hash: str = Field(..., description="Content hash used as cache key")


class ScanCache(BaseCache[CachedScanResult]):
    """Cache for secrets scan results.

    Uses content hash as key - automatically invalidates when file changes.
    24-hour TTL since scan results are expensive to compute.

    Example:
        ```python
        cache = ScanCache(redis)

        # Check cache first (auto-invalidating on content change)
        cached = await cache.get_by_content(file_content)
        if cached:
            return cached

        # Expensive scan operation
        result = await scan_file(file_content)

        # Cache for future
        await cache.set_by_content(file_content, result)
        ```
    """

    def __init__(
        self,
        redis: Optional["Redis"],
        ttl_seconds: int = DEFAULT_SCAN_TTL_SECONDS,
    ):
        """Initialize the scan cache.

        Args:
            redis: Async Redis client (can be None for graceful degradation)
            ttl_seconds: TTL for cached entries (default: 86400 = 24 hours)
        """
        super().__init__(
            redis=redis,
            prefix="secrets:scan:",
            ttl_seconds=ttl_seconds,
            model_class=CachedScanResult,
        )

    @staticmethod
    def hash_content(content: str) -> str:
        """Generate MD5 hash of content for cache key.

        Args:
            content: File content to hash

        Returns:
            MD5 hex digest
        """
        return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()

    async def get_by_content(self, content: str) -> Optional[CachedScanResult]:
        """Get scan result by file content (auto-invalidating).

        Args:
            content: File content to lookup

        Returns:
            Cached scan result or None
        """
        content_hash = self.hash_content(content)
        result = await self.get(content_hash)

        if result:
            logger.debug(
                "Scan cache hit",
                extra={
                    "content_hash": content_hash[:16],
                    "has_secrets": result.has_secrets,
                    "total_secrets": result.total_secrets,
                },
            )

        return result

    async def set_by_content(
        self,
        content: str,
        has_secrets: bool,
        total_secrets: int,
        by_risk_level: Optional[Dict[str, int]] = None,
        by_type: Optional[Dict[str, int]] = None,
        matches: Optional[List[CachedSecretMatch]] = None,
    ) -> bool:
        """Cache scan result keyed by content hash.

        Args:
            content: File content that was scanned
            has_secrets: Whether secrets were found
            total_secrets: Count of secrets found
            by_risk_level: Count by risk level
            by_type: Count by secret type
            matches: List of secret matches

        Returns:
            True if successfully cached
        """
        content_hash = self.hash_content(content)

        result = CachedScanResult(
            has_secrets=has_secrets,
            total_secrets=total_secrets,
            by_risk_level=by_risk_level or {},
            by_type=by_type or {},
            matches=matches or [],
            file_hash=content_hash,
        )

        success = await self.set(content_hash, result)

        if success:
            logger.debug(
                "Cached scan result",
                extra={
                    "content_hash": content_hash[:16],
                    "has_secrets": has_secrets,
                    "total_secrets": total_secrets,
                    "ttl": self.ttl,
                },
            )

        return success

    async def set_from_scan_result(
        self,
        content: str,
        scan_result: "SecretsScanResult",  # type: ignore[name-defined]
    ) -> bool:
        """Cache from a SecretsScanResult object.

        Convenience method to cache directly from scan output.

        Args:
            content: File content that was scanned
            scan_result: Result from SecretsScanner.scan_string()

        Returns:
            True if successfully cached
        """
        # Convert SecretMatch objects to cached format
        matches = []
        for match in scan_result.secrets_found[:50]:  # Limit to 50 matches
            matches.append(
                CachedSecretMatch(
                    secret_type=match.secret_type,
                    line_number=match.line_number,
                    risk_level=match.risk_level,
                    remediation=match.remediation,
                )
            )

        return await self.set_by_content(
            content=content,
            has_secrets=scan_result.has_secrets,
            total_secrets=scan_result.total_secrets,
            by_risk_level=scan_result.by_risk_level,
            by_type=scan_result.by_type,
            matches=matches,
        )

    async def invalidate_by_content(self, content: str) -> bool:
        """Invalidate cache entry for specific content.

        Args:
            content: File content to invalidate

        Returns:
            True if successfully invalidated
        """
        content_hash = self.hash_content(content)
        return await self.delete(content_hash)

"""TTL-based cache for fix preview results.

Preview results are cached for 15 minutes since:
- Previews are ephemeral and tied to current fix state
- Re-running previews is acceptable if cache expires
- Short TTL reduces risk of stale validation results
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from repotoire.cache.base import BaseCache
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from repotoire.api.models import PreviewResult

logger = get_logger(__name__)

# Default TTL: 15 minutes
DEFAULT_PREVIEW_TTL_SECONDS = 900


def _get_preview_result_class():
    """Lazy import to avoid circular dependency."""
    from repotoire.api.models import PreviewResult

    return PreviewResult


class PreviewCache(BaseCache[Any]):
    """Cache for fix preview results.

    TTL-based cache with 15-minute expiration.
    Previews are ephemeral - acceptable to recompute on miss.

    Keys are fix IDs, values are serialized PreviewResult objects.

    Example:
        ```python
        cache = PreviewCache(redis)

        # Cache a preview result
        await cache.set_preview(str(fix_id), result)

        # Get cached result
        cached = await cache.get_preview(str(fix_id))
        if cached:
            return cached

        # Invalidate when fix changes
        await cache.invalidate(str(fix_id))
        ```
    """

    def __init__(
        self,
        redis: Optional["Redis"],
        ttl_seconds: int = DEFAULT_PREVIEW_TTL_SECONDS,
    ):
        """Initialize the preview cache.

        Args:
            redis: Async Redis client (can be None for graceful degradation)
            ttl_seconds: TTL for cached entries (default: 900 = 15 minutes)
        """
        # Use lazy import to avoid circular dependency
        model_class = _get_preview_result_class()
        super().__init__(
            redis=redis,
            prefix="fix:preview:",
            ttl_seconds=ttl_seconds,
            model_class=model_class,
        )

    async def get_preview(self, fix_id: str) -> Optional["PreviewResult"]:
        """Get cached preview result for a fix.

        Args:
            fix_id: UUID of the fix as string

        Returns:
            Cached PreviewResult or None if not cached
        """
        return await self.get(fix_id)

    async def set_preview(self, fix_id: str, result: "PreviewResult") -> bool:
        """Cache a preview result.

        Args:
            fix_id: UUID of the fix as string
            result: PreviewResult to cache

        Returns:
            True if successfully cached
        """
        success = await self.set(fix_id, result)
        if success:
            logger.debug(
                "Cached preview result",
                extra={
                    "fix_id": fix_id,
                    "success": result.success,
                    "ttl": self.ttl,
                },
            )
        return success

    async def invalidate(self, fix_id: str) -> bool:
        """Invalidate cached preview when fix is modified.

        Args:
            fix_id: UUID of the fix as string

        Returns:
            True if successfully invalidated
        """
        success = await self.delete(fix_id)
        if success:
            logger.debug("Invalidated preview cache", extra={"fix_id": fix_id})
        return success

    async def get_with_hash_check(
        self,
        fix_id: str,
        current_hash: str,
    ) -> Optional["PreviewResult"]:
        """Get cached preview only if hash matches.

        This is useful when the fix content may have changed since caching.
        The hash is stored in the cached_at field as "{timestamp}:{hash}".

        Args:
            fix_id: UUID of the fix as string
            current_hash: Current content hash of the fix

        Returns:
            Cached PreviewResult if hash matches, None otherwise
        """
        cached = await self.get(fix_id)
        if not cached:
            return None

        # Check if cached_at contains hash info
        if cached.cached_at and ":" in cached.cached_at:
            # Split only on the first colon after the timestamp
            # Format is: 2025-01-01T00:00:00:hash
            # We need to find the hash at the end
            parts = cached.cached_at.rsplit(":", 1)
            if len(parts) == 2:
                cached_hash = parts[-1]
                if cached_hash == current_hash:
                    return cached

                # Hash mismatch - invalidate stale cache
                logger.debug(
                    "Preview cache hash mismatch",
                    extra={
                        "fix_id": fix_id,
                        "cached_hash": cached_hash[:8] if len(cached_hash) >= 8 else cached_hash,
                        "current_hash": current_hash[:8],
                    },
                )
                await self.invalidate(fix_id)
                return None

        # No hash in cached_at - return the cached result
        return cached

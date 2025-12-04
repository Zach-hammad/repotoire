"""Redis caching layer for Repotoire.

This module provides a unified caching layer with:
- PreviewCache: TTL-based cache for fix previews (15 min)
- ScanCache: Content-hash based cache for secrets scans (24 hours)
- TwoTierSkillCache: L1 (local) + L2 (Redis) cache for skills (1 hour)

All caches support graceful degradation when Redis is unavailable.

Usage:
    ```python
    from repotoire.cache import get_cache_manager, PreviewCache, ScanCache

    # Get cache manager (manages all cache instances)
    manager = await get_cache_manager()

    # Use individual caches
    preview = await manager.preview.get_preview(fix_id)
    scan_result = await manager.scan.get_by_content(content)
    skill = await manager.skill.get(skill_id)
    ```

FastAPI Dependency Injection:
    ```python
    from repotoire.cache import get_preview_cache, get_scan_cache

    @router.post("/fixes/{fix_id}/preview")
    async def preview_fix(
        fix_id: UUID,
        cache: PreviewCache = Depends(get_preview_cache),
    ):
        cached = await cache.get_preview(str(fix_id))
        if cached:
            return cached
        # ... run preview
    ```
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

import redis.asyncio as aioredis

from repotoire.cache.base import BaseCache, CacheMetrics
from repotoire.cache.preview import PreviewCache, DEFAULT_PREVIEW_TTL_SECONDS
from repotoire.cache.scan import (
    ScanCache,
    CachedScanResult,
    CachedSecretMatch,
    DEFAULT_SCAN_TTL_SECONDS,
)
from repotoire.cache.skill import (
    TwoTierSkillCache,
    CachedSkill,
    DEFAULT_SKILL_TTL_SECONDS,
)
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

__all__ = [
    # Base classes
    "BaseCache",
    "CacheMetrics",
    # Cache implementations
    "PreviewCache",
    "ScanCache",
    "TwoTierSkillCache",
    # Models
    "CachedScanResult",
    "CachedSecretMatch",
    "CachedSkill",
    # Constants
    "DEFAULT_PREVIEW_TTL_SECONDS",
    "DEFAULT_SCAN_TTL_SECONDS",
    "DEFAULT_SKILL_TTL_SECONDS",
    # Manager
    "CacheManager",
    "get_cache_manager",
    "close_cache_manager",
    # FastAPI dependencies
    "get_preview_cache",
    "get_scan_cache",
    "get_skill_cache",
    # Redis helpers
    "get_redis_client",
    "close_redis_client",
]


class CacheManager:
    """Manages all cache instances.

    Provides a single point of access to all caches and their metrics.

    Example:
        ```python
        manager = CacheManager(redis)

        # Access caches
        preview = await manager.preview.get_preview(fix_id)
        scan = await manager.scan.get_by_content(content)

        # Get metrics for monitoring
        metrics = manager.get_metrics()
        ```
    """

    def __init__(self, redis: Optional["Redis"] = None):
        """Initialize cache manager.

        Args:
            redis: Optional Redis client (caches degrade gracefully without it)
        """
        self.redis = redis
        self.preview = PreviewCache(redis)
        self.scan = ScanCache(redis)
        self.skill = TwoTierSkillCache(redis)

    def get_metrics(self) -> dict:
        """Get metrics from all caches for monitoring.

        Returns:
            Dictionary with metrics for each cache
        """
        return {
            "preview": self.preview.metrics.to_dict(),
            "scan": self.scan.metrics.to_dict(),
            "skill": self.skill.metrics.to_dict(),
        }

    def get_summary(self) -> dict:
        """Get summary of cache health.

        Returns:
            Dictionary with high-level cache stats
        """
        metrics = self.get_metrics()
        return {
            "redis_connected": self.redis is not None,
            "total_hits": sum(m.get("total_hits", 0) for m in metrics.values()),
            "total_misses": sum(m.get("misses", 0) for m in metrics.values()),
            "total_errors": sum(m.get("total_errors", 0) for m in metrics.values()),
            "caches": {
                name: {
                    "hit_rate": m.get("hit_rate", 0),
                    "errors": m.get("total_errors", 0),
                }
                for name, m in metrics.items()
            },
        }


# Singleton instances
_redis_client: Optional["Redis"] = None
_cache_manager: Optional[CacheManager] = None


async def get_redis_client() -> Optional["Redis"]:
    """Get or create shared Redis client for caching.

    Uses REDIS_URL environment variable.

    Returns:
        Redis client or None if unavailable
    """
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        logger.debug("REDIS_URL not set, caching disabled")
        return None

    try:
        _redis_client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await _redis_client.ping()
        logger.info("Redis client connected for caching")
        return _redis_client

    except Exception as e:
        logger.warning(f"Failed to connect to Redis for caching: {e}")
        _redis_client = None
        return None


async def close_redis_client() -> None:
    """Close shared Redis client."""
    global _redis_client

    if _redis_client is not None:
        try:
            await _redis_client.close()
        except Exception as e:
            logger.warning(f"Error closing Redis client: {e}")
        _redis_client = None
        logger.debug("Cache Redis client closed")


async def get_cache_manager() -> CacheManager:
    """Get or create the cache manager.

    Returns:
        CacheManager instance with all caches initialized
    """
    global _cache_manager

    if _cache_manager is None:
        redis = await get_redis_client()
        _cache_manager = CacheManager(redis)
        logger.info(
            "Cache manager initialized",
            extra={"redis_connected": redis is not None},
        )

    return _cache_manager


async def close_cache_manager() -> None:
    """Close cache manager and Redis client."""
    global _cache_manager

    if _cache_manager is not None:
        # Clear L1 caches
        _cache_manager.skill.invalidate_local()
        _cache_manager = None

    await close_redis_client()
    logger.debug("Cache manager closed")


# FastAPI Dependency Functions
async def get_preview_cache() -> PreviewCache:
    """FastAPI dependency for preview cache.

    Returns:
        PreviewCache instance
    """
    manager = await get_cache_manager()
    return manager.preview


async def get_scan_cache() -> ScanCache:
    """FastAPI dependency for scan cache.

    Returns:
        ScanCache instance
    """
    manager = await get_cache_manager()
    return manager.scan


async def get_skill_cache() -> TwoTierSkillCache:
    """FastAPI dependency for skill cache.

    Returns:
        TwoTierSkillCache instance
    """
    manager = await get_cache_manager()
    return manager.skill

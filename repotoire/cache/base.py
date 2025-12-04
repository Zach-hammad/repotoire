"""Base cache class with metrics and graceful degradation.

This module provides the foundation for Redis-backed caches with:
- Async operations via redis.asyncio
- Structured metrics tracking (hits, misses, errors)
- Graceful degradation on Redis failures
- Pydantic model serialization
"""

from __future__ import annotations

import time
from abc import ABC
from typing import TYPE_CHECKING, Generic, Optional, TypeVar

from pydantic import BaseModel

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class CacheMetrics:
    """Track cache performance metrics.

    Tracks hits, misses, errors, and latency for cache operations.
    Logs metrics via structlog for observability.

    Attributes:
        cache_name: Identifier for this cache (used in logs)
        hits: Total cache hits (by tier for two-tier caches)
        misses: Total cache misses
        errors: Total errors (by type)
        latency_samples: Recent latency measurements
    """

    def __init__(self, cache_name: str):
        """Initialize metrics for a cache.

        Args:
            cache_name: Identifier for this cache instance
        """
        self.cache_name = cache_name
        self.hits: dict[str, int] = {"redis": 0, "local": 0}
        self.misses = 0
        self.errors: dict[str, int] = {"connection": 0, "serialization": 0, "other": 0}
        self._latency_samples: list[tuple[str, float]] = []  # (operation, ms)
        self._max_samples = 1000

    def record_hit(self, tier: str = "redis") -> None:
        """Record a cache hit.

        Args:
            tier: Cache tier that served the hit ("redis" or "local")
        """
        self.hits[tier] = self.hits.get(tier, 0) + 1
        logger.debug(
            "Cache hit",
            extra={
                "cache": self.cache_name,
                "tier": tier,
                "total_hits": sum(self.hits.values()),
                "misses": self.misses,
            },
        )

    def record_miss(self) -> None:
        """Record a cache miss."""
        self.misses += 1
        logger.debug(
            "Cache miss",
            extra={
                "cache": self.cache_name,
                "total_hits": sum(self.hits.values()),
                "misses": self.misses,
            },
        )

    def record_error(self, error_type: str, error: Exception) -> None:
        """Record a cache error.

        Args:
            error_type: Category of error ("connection", "serialization", "other")
            error: The exception that occurred
        """
        self.errors[error_type] = self.errors.get(error_type, 0) + 1
        logger.warning(
            "Cache error",
            extra={
                "cache": self.cache_name,
                "error_type": error_type,
                "error": str(error),
                "total_errors": sum(self.errors.values()),
            },
        )

    def record_latency(self, operation: str, latency_ms: float) -> None:
        """Record operation latency.

        Args:
            operation: Operation type ("get", "set", "delete")
            latency_ms: Latency in milliseconds
        """
        if len(self._latency_samples) >= self._max_samples:
            # Remove oldest samples
            self._latency_samples = self._latency_samples[-self._max_samples // 2 :]

        self._latency_samples.append((operation, latency_ms))

        # Log slow operations
        if latency_ms > 100:
            logger.warning(
                "Slow cache operation",
                extra={
                    "cache": self.cache_name,
                    "operation": operation,
                    "latency_ms": latency_ms,
                },
            )

    @property
    def total_hits(self) -> int:
        """Get total hits across all tiers."""
        return sum(self.hits.values())

    @property
    def hit_rate(self) -> float:
        """Calculate hit rate as a percentage.

        Returns:
            Hit rate between 0.0 and 1.0
        """
        total = self.total_hits + self.misses
        return self.total_hits / total if total > 0 else 0.0

    @property
    def total_errors(self) -> int:
        """Get total errors across all types."""
        return sum(self.errors.values())

    def get_avg_latency(self, operation: Optional[str] = None) -> float:
        """Get average latency for operations.

        Args:
            operation: Filter to specific operation, or None for all

        Returns:
            Average latency in milliseconds
        """
        if not self._latency_samples:
            return 0.0

        samples = [
            lat
            for op, lat in self._latency_samples
            if operation is None or op == operation
        ]
        return sum(samples) / len(samples) if samples else 0.0

    def to_dict(self) -> dict:
        """Export metrics as dictionary.

        Returns:
            Dictionary with all metrics
        """
        return {
            "cache": self.cache_name,
            "hits": self.hits.copy(),
            "total_hits": self.total_hits,
            "misses": self.misses,
            "hit_rate": self.hit_rate,
            "errors": self.errors.copy(),
            "total_errors": self.total_errors,
            "avg_latency_ms": {
                "get": self.get_avg_latency("get"),
                "set": self.get_avg_latency("set"),
                "delete": self.get_avg_latency("delete"),
            },
        }


class BaseCache(ABC, Generic[T]):
    """Base class for Redis-backed caches with graceful degradation.

    Provides async cache operations with:
    - Automatic serialization via Pydantic models
    - TTL-based expiration
    - Metrics tracking
    - Graceful degradation on Redis failures

    Type Parameters:
        T: Pydantic model type for cached values

    Example:
        ```python
        class MyCache(BaseCache[MyModel]):
            def __init__(self, redis: Redis):
                super().__init__(
                    redis=redis,
                    prefix="my:cache:",
                    ttl_seconds=300,
                    model_class=MyModel,
                )
        ```
    """

    def __init__(
        self,
        redis: Optional["Redis"],
        prefix: str,
        ttl_seconds: int,
        model_class: type[T],
    ):
        """Initialize the cache.

        Args:
            redis: Async Redis client (can be None for graceful degradation)
            prefix: Key prefix for all cache entries
            ttl_seconds: Time-to-live for cached entries
            model_class: Pydantic model class for values
        """
        self.redis = redis
        self.prefix = prefix
        self.ttl = ttl_seconds
        self.model_class = model_class
        self.metrics = CacheMetrics(prefix.rstrip(":"))

    def _make_key(self, key: str) -> str:
        """Create full Redis key with prefix.

        Args:
            key: Cache key without prefix

        Returns:
            Full Redis key with prefix
        """
        return f"{self.prefix}{key}"

    async def get(self, key: str) -> Optional[T]:
        """Get value from cache.

        Returns None on miss or error (graceful degradation).

        Args:
            key: Cache key

        Returns:
            Cached value or None
        """
        if self.redis is None:
            self.metrics.record_miss()
            return None

        redis_key = self._make_key(key)
        start = time.perf_counter()

        try:
            data = await self.redis.get(redis_key)
            latency_ms = (time.perf_counter() - start) * 1000
            self.metrics.record_latency("get", latency_ms)

            if data:
                try:
                    value = self.model_class.model_validate_json(data)
                    self.metrics.record_hit()
                    return value
                except Exception as e:
                    self.metrics.record_error("serialization", e)
                    return None

            self.metrics.record_miss()
            return None

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            self.metrics.record_latency("get", latency_ms)
            self.metrics.record_error("connection", e)
            return None

    async def set(self, key: str, value: T) -> bool:
        """Set value in cache.

        Returns False on error (graceful degradation).

        Args:
            key: Cache key
            value: Value to cache (must be Pydantic model)

        Returns:
            True if successfully cached, False otherwise
        """
        if self.redis is None:
            return False

        redis_key = self._make_key(key)
        start = time.perf_counter()

        try:
            json_data = value.model_dump_json()
            await self.redis.setex(redis_key, self.ttl, json_data)
            latency_ms = (time.perf_counter() - start) * 1000
            self.metrics.record_latency("set", latency_ms)
            return True

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            self.metrics.record_latency("set", latency_ms)

            # Distinguish serialization errors from connection errors
            if "json" in str(e).lower() or "serialize" in str(e).lower():
                self.metrics.record_error("serialization", e)
            else:
                self.metrics.record_error("connection", e)
            return False

    async def delete(self, key: str) -> bool:
        """Delete value from cache.

        Args:
            key: Cache key

        Returns:
            True if successfully deleted, False otherwise
        """
        if self.redis is None:
            return False

        redis_key = self._make_key(key)
        start = time.perf_counter()

        try:
            await self.redis.delete(redis_key)
            latency_ms = (time.perf_counter() - start) * 1000
            self.metrics.record_latency("delete", latency_ms)
            return True

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            self.metrics.record_latency("delete", latency_ms)
            self.metrics.record_error("connection", e)
            return False

    async def exists(self, key: str) -> bool:
        """Check if key exists in cache.

        Args:
            key: Cache key

        Returns:
            True if key exists, False otherwise
        """
        if self.redis is None:
            return False

        redis_key = self._make_key(key)
        try:
            return bool(await self.redis.exists(redis_key))
        except Exception as e:
            self.metrics.record_error("connection", e)
            return False

    async def clear_prefix(self) -> int:
        """Clear all keys with this cache's prefix.

        Returns:
            Number of keys deleted
        """
        if self.redis is None:
            return 0

        try:
            pattern = f"{self.prefix}*"
            keys = []
            async for key in self.redis.scan_iter(match=pattern):
                keys.append(key)

            if keys:
                await self.redis.delete(*keys)

            logger.info(
                f"Cleared {len(keys)} keys from cache",
                extra={"cache": self.metrics.cache_name, "count": len(keys)},
            )
            return len(keys)

        except Exception as e:
            self.metrics.record_error("connection", e)
            return 0

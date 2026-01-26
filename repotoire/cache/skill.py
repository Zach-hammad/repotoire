"""Two-tier cache for loaded skill code.

Two-tier architecture provides:
- L1: Local in-memory dict (fastest, per-process)
- L2: Redis (shared across workers)

On miss: Check L1 -> Check L2 -> Load from disk -> Populate both

Skills are cached by ID with 1-hour TTL since:
- Skills change infrequently during runtime
- Loading from disk is relatively slow
- Memory footprint is reasonable for typical skill counts

REPO-500: L1 cache has max size limit and periodic eviction of stale entries
to prevent unbounded memory growth.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Dict, Optional

from pydantic import BaseModel, Field

from repotoire.cache.base import CacheMetrics
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

# Default TTL: 1 hour
DEFAULT_SKILL_TTL_SECONDS = 3600

# REPO-500: Default max L1 cache size (prevents unbounded memory growth)
DEFAULT_MAX_L1_SIZE = 1000


class CachedSkill(BaseModel):
    """Cached representation of a loaded skill.

    Contains the skill code and metadata needed for execution.
    The version field enables L1/L2 cache coherence checking.
    """

    skill_id: str = Field(..., description="Unique skill identifier")
    skill_name: str = Field(..., description="Human-readable skill name")
    skill_code: str = Field(..., description="Python source code")
    code_hash: str = Field(..., description="Hash of skill code for change detection")
    loaded_at: str = Field(..., description="ISO timestamp when skill was loaded")
    source_path: Optional[str] = Field(None, description="Path to skill source file")
    version: int = Field(default=1, description="Version number for L1/L2 cache coherence")


class TwoTierSkillCache:
    """Two-tier cache for loaded skills.

    L1: Local in-memory dict (fastest, per-process)
    L2: Redis (shared across workers)

    On miss: Check L1 -> Check L2 -> Load from disk -> Populate both

    Example:
        ```python
        cache = TwoTierSkillCache(redis)

        # Try to get from cache
        cached = await cache.get("my_skill")
        if cached:
            return cached

        # Load skill from disk and cache it
        skill = load_skill_from_disk("my_skill")
        await cache.set("my_skill", skill)
        ```
    """

    def __init__(
        self,
        redis: Optional["Redis"],
        ttl_seconds: int = DEFAULT_SKILL_TTL_SECONDS,
        max_l1_size: int = DEFAULT_MAX_L1_SIZE,
    ):
        """Initialize the two-tier skill cache.

        Args:
            redis: Async Redis client (can be None for L1-only mode)
            ttl_seconds: TTL for L2 (Redis) entries (default: 3600 = 1 hour)
            max_l1_size: Maximum entries in L1 cache (default: 1000)
        """
        self.redis = redis
        self.prefix = "skill:"
        self.ttl = ttl_seconds
        self.max_l1_size = max_l1_size
        self._local: Dict[str, CachedSkill] = {}
        self._local_timestamps: Dict[str, float] = {}  # Track when items were added
        self.metrics = CacheMetrics("skill")

    def _make_key(self, skill_id: str) -> str:
        """Create Redis key for skill.

        Args:
            skill_id: Skill identifier

        Returns:
            Full Redis key
        """
        return f"{self.prefix}{skill_id}"

    def _is_local_fresh(self, skill_id: str) -> bool:
        """Check if local cache entry is still fresh.

        Local cache entries expire after TTL to stay in sync with Redis.

        Args:
            skill_id: Skill identifier

        Returns:
            True if entry is fresh
        """
        if skill_id not in self._local_timestamps:
            return False
        age = time.time() - self._local_timestamps[skill_id]
        return age < self.ttl

    def _evict_stale_entries(self) -> int:
        """REPO-500: Evict stale entries from L1 cache.

        Called periodically to prevent memory growth from expired entries.

        Returns:
            Number of entries evicted
        """
        now = time.time()
        stale_keys = [
            key for key, ts in self._local_timestamps.items()
            if now - ts >= self.ttl
        ]
        for key in stale_keys:
            self._local.pop(key, None)
            self._local_timestamps.pop(key, None)

        if stale_keys:
            logger.debug(f"Evicted {len(stale_keys)} stale L1 cache entries")
        return len(stale_keys)

    def _evict_lru_if_needed(self) -> int:
        """REPO-500: Evict least recently used entries if L1 is at max size.

        Evicts oldest entries (by timestamp) to make room for new entries.

        Returns:
            Number of entries evicted
        """
        if len(self._local) < self.max_l1_size:
            return 0

        # First try to evict stale entries
        evicted = self._evict_stale_entries()
        if len(self._local) < self.max_l1_size:
            return evicted

        # Still too full - evict oldest entries until we're at 80% capacity
        target_size = int(self.max_l1_size * 0.8)
        to_evict = len(self._local) - target_size

        if to_evict > 0:
            # Sort by timestamp (oldest first)
            sorted_keys = sorted(
                self._local_timestamps.items(),
                key=lambda x: x[1]
            )
            for key, _ in sorted_keys[:to_evict]:
                self._local.pop(key, None)
                self._local_timestamps.pop(key, None)
                evicted += 1

            logger.debug(f"Evicted {evicted} L1 cache entries (LRU)")

        return evicted

    async def _get_l2_version(self, skill_id: str) -> Optional[int]:
        """Get the version of a skill from L2 cache.

        Used for L1/L2 coherence checking to detect stale L1 entries.

        Args:
            skill_id: Skill identifier

        Returns:
            Version number from L2, or None if not found/error
        """
        if self.redis is None:
            return None

        try:
            redis_key = self._make_key(skill_id)
            data = await self.redis.get(redis_key)
            if data:
                skill = CachedSkill.model_validate_json(data)
                return skill.version
        except Exception:
            pass  # Silently fail for version check
        return None

    async def get(self, skill_id: str) -> Optional[CachedSkill]:
        """Get skill, checking L1 then L2.

        Includes L1/L2 version coherence checking - if L2 has a newer version
        than L1, the stale L1 entry is invalidated.

        Args:
            skill_id: Skill identifier

        Returns:
            Cached skill or None
        """
        # L1: Local cache (fastest)
        if skill_id in self._local and self._is_local_fresh(skill_id):
            local_skill = self._local[skill_id]

            # Check L2 version for coherence (if Redis available)
            l2_version = await self._get_l2_version(skill_id)
            if l2_version is not None and l2_version > local_skill.version:
                # L1 is stale - invalidate it and fall through to L2 fetch
                logger.debug(
                    "L1 skill cache stale (L2 has newer version), invalidating",
                    extra={
                        "skill_id": skill_id,
                        "l1_version": local_skill.version,
                        "l2_version": l2_version,
                    },
                )
                self._local.pop(skill_id, None)
                self._local_timestamps.pop(skill_id, None)
            else:
                self.metrics.record_hit(tier="local")
                logger.debug(
                    "L1 skill cache hit",
                    extra={"skill_id": skill_id},
                )
                return local_skill

        # L2: Redis cache
        if self.redis is not None:
            start = time.perf_counter()
            try:
                redis_key = self._make_key(skill_id)
                data = await self.redis.get(redis_key)
                latency_ms = (time.perf_counter() - start) * 1000
                self.metrics.record_latency("get", latency_ms)

                if data:
                    try:
                        skill = CachedSkill.model_validate_json(data)
                        # Populate L1 for next access
                        self._local[skill_id] = skill
                        self._local_timestamps[skill_id] = time.time()
                        self.metrics.record_hit(tier="redis")
                        logger.debug(
                            "L2 skill cache hit, promoted to L1",
                            extra={"skill_id": skill_id, "latency_ms": latency_ms},
                        )
                        return skill
                    except Exception as e:
                        self.metrics.record_error("serialization", e)

            except Exception as e:
                self.metrics.record_error("connection", e)

        self.metrics.record_miss()
        logger.debug("Skill cache miss", extra={"skill_id": skill_id})
        return None

    async def set(self, skill_id: str, skill: CachedSkill) -> bool:
        """Set skill in both L1 and L2.

        Args:
            skill_id: Skill identifier
            skill: Skill to cache

        Returns:
            True if successfully cached (L1 always succeeds)
        """
        # REPO-500: Evict entries if needed before adding new one
        self._evict_lru_if_needed()

        # Always update L1
        self._local[skill_id] = skill
        self._local_timestamps[skill_id] = time.time()

        # Try to update L2
        l2_success = True
        if self.redis is not None:
            start = time.perf_counter()
            try:
                redis_key = self._make_key(skill_id)
                await self.redis.setex(redis_key, self.ttl, skill.model_dump_json())
                latency_ms = (time.perf_counter() - start) * 1000
                self.metrics.record_latency("set", latency_ms)
                logger.debug(
                    "Cached skill in L1 and L2",
                    extra={
                        "skill_id": skill_id,
                        "ttl": self.ttl,
                        "latency_ms": latency_ms,
                    },
                )
            except Exception as e:
                self.metrics.record_error("connection", e)
                l2_success = False
                logger.debug(
                    "Cached skill in L1 only (L2 failed)",
                    extra={"skill_id": skill_id, "error": str(e)},
                )
        else:
            logger.debug(
                "Cached skill in L1 only (no Redis)",
                extra={"skill_id": skill_id},
            )

        return l2_success

    def get_local(self, skill_id: str) -> Optional[CachedSkill]:
        """Get from L1 only (synchronous).

        Args:
            skill_id: Skill identifier

        Returns:
            Cached skill or None
        """
        if skill_id in self._local and self._is_local_fresh(skill_id):
            return self._local[skill_id]
        return None

    def invalidate_local(self, skill_id: Optional[str] = None) -> int:
        """Invalidate L1 cache (e.g., on skill file change).

        Args:
            skill_id: Specific skill to invalidate, or None for all

        Returns:
            Number of entries cleared
        """
        if skill_id:
            if skill_id in self._local:
                del self._local[skill_id]
                self._local_timestamps.pop(skill_id, None)
                logger.debug("Invalidated L1 cache", extra={"skill_id": skill_id})
                return 1
            return 0
        else:
            count = len(self._local)
            self._local.clear()
            self._local_timestamps.clear()
            logger.debug("Cleared all L1 cache", extra={"count": count})
            return count

    async def invalidate(self, skill_id: str) -> bool:
        """Invalidate both L1 and L2.

        Args:
            skill_id: Skill to invalidate

        Returns:
            True if L2 invalidation succeeded
        """
        # Clear L1
        self._local.pop(skill_id, None)
        self._local_timestamps.pop(skill_id, None)

        # Clear L2
        if self.redis is not None:
            start = time.perf_counter()
            try:
                redis_key = self._make_key(skill_id)
                await self.redis.delete(redis_key)
                latency_ms = (time.perf_counter() - start) * 1000
                self.metrics.record_latency("delete", latency_ms)
                logger.debug(
                    "Invalidated skill from L1 and L2",
                    extra={"skill_id": skill_id},
                )
                return True
            except Exception as e:
                self.metrics.record_error("connection", e)
                return False

        return True

    async def clear_all(self) -> int:
        """Clear all skills from both L1 and L2.

        Returns:
            Number of L2 entries cleared
        """
        # Clear L1
        l1_count = len(self._local)
        self._local.clear()
        self._local_timestamps.clear()

        # Clear L2
        l2_count = 0
        if self.redis is not None:
            try:
                pattern = f"{self.prefix}*"
                keys = []
                async for key in self.redis.scan_iter(match=pattern):
                    keys.append(key)

                if keys:
                    await self.redis.delete(*keys)
                    l2_count = len(keys)

                logger.info(
                    "Cleared skill cache",
                    extra={"l1_count": l1_count, "l2_count": l2_count},
                )

            except Exception as e:
                self.metrics.record_error("connection", e)

        return l2_count

    @property
    def local_size(self) -> int:
        """Get number of entries in L1 cache."""
        return len(self._local)

    def get_stats(self) -> dict:
        """Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        return {
            "local_size": self.local_size,
            "ttl_seconds": self.ttl,
            "has_redis": self.redis is not None,
            **self.metrics.to_dict(),
        }

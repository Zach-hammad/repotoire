"""Redis-based push debouncer for webhook events.

Prevents duplicate analyses when rapid pushes occur to the same repository.
Uses Redis SET NX with TTL for atomic debouncing.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from redis import Redis

logger = get_logger(__name__)

# Default debounce window in seconds
DEFAULT_DEBOUNCE_SECONDS = 60


class PushDebouncer:
    """Redis-based debouncer for push events.

    Uses Redis SETNX with TTL to ensure only the first push in a time window
    triggers analysis. Subsequent pushes within the window are debounced.

    Gracefully degrades if Redis is unavailable - in that case, all pushes
    trigger analysis (no debouncing).

    Attributes:
        redis: Redis client (sync)
        prefix: Key prefix for debounce keys
        ttl_seconds: Debounce window in seconds

    Example:
        ```python
        debouncer = PushDebouncer(redis_client)

        # First push - returns True, analysis should trigger
        if debouncer.should_analyze(repo_id=12345):
            analyze_repository.delay(...)

        # Second push within 60s - returns False, skip analysis
        if debouncer.should_analyze(repo_id=12345):
            analyze_repository.delay(...)  # Not reached
        ```
    """

    def __init__(
        self,
        redis: Optional["Redis"] = None,
        prefix: str = "push:debounce:",
        ttl_seconds: int = DEFAULT_DEBOUNCE_SECONDS,
    ):
        """Initialize the debouncer.

        Args:
            redis: Redis client. If None, debouncing is disabled (all pushes trigger).
            prefix: Key prefix for Redis keys.
            ttl_seconds: Debounce window - pushes within this window are deduplicated.
        """
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds

    def _make_key(self, repo_id: int) -> str:
        """Create Redis key for a repository.

        Args:
            repo_id: GitHub repository ID.

        Returns:
            Full Redis key with prefix.
        """
        return f"{self.prefix}{repo_id}"

    def should_analyze(self, repo_id: int) -> bool:
        """Check if analysis should be triggered for this push.

        Uses Redis SETNX to atomically check-and-set the debounce key.
        Returns True only for the first push in the debounce window.

        If Redis is unavailable, always returns True (no debouncing).

        Args:
            repo_id: GitHub repository ID.

        Returns:
            True if analysis should trigger, False if debounced.
        """
        if self.redis is None:
            logger.debug(
                "Redis unavailable, skipping debounce",
                extra={"repo_id": repo_id},
            )
            return True

        key = self._make_key(repo_id)

        try:
            # SETNX returns True if key was set (first push), False if already exists
            was_set = self.redis.set(
                key,
                "1",
                nx=True,  # Only set if key doesn't exist
                ex=self.ttl_seconds,  # Expire after TTL
            )

            if was_set:
                logger.info(
                    "Push accepted (not debounced)",
                    extra={"repo_id": repo_id, "ttl_seconds": self.ttl_seconds},
                )
                return True
            else:
                logger.info(
                    "Push debounced",
                    extra={"repo_id": repo_id, "key": key},
                )
                return False

        except Exception as e:
            # Redis failure - gracefully degrade to no debouncing
            logger.warning(
                "Redis error during debounce check, allowing analysis",
                extra={"repo_id": repo_id, "error": str(e)},
            )
            return True

    def clear(self, repo_id: int) -> bool:
        """Clear debounce key for a repository.

        Useful for testing or forcing immediate re-analysis.

        Args:
            repo_id: GitHub repository ID.

        Returns:
            True if key was cleared, False otherwise.
        """
        if self.redis is None:
            return False

        key = self._make_key(repo_id)
        try:
            return bool(self.redis.delete(key))
        except Exception as e:
            logger.warning(
                "Redis error clearing debounce key",
                extra={"repo_id": repo_id, "error": str(e)},
            )
            return False

    def get_ttl(self, repo_id: int) -> Optional[int]:
        """Get remaining TTL for a debounce key.

        Useful for debugging and testing.

        Args:
            repo_id: GitHub repository ID.

        Returns:
            Remaining TTL in seconds, or None if key doesn't exist.
        """
        if self.redis is None:
            return None

        key = self._make_key(repo_id)
        try:
            ttl = self.redis.ttl(key)
            return ttl if ttl > 0 else None
        except Exception:
            return None


def get_push_debouncer() -> PushDebouncer:
    """Factory function to create a PushDebouncer with Redis from environment.

    Returns a debouncer with Redis client configured from REDIS_URL env var.
    If REDIS_URL is not set or connection fails, returns a debouncer that
    gracefully degrades (no debouncing).

    Returns:
        PushDebouncer instance.
    """
    redis_url = os.environ.get("REDIS_URL")

    if not redis_url:
        logger.warning("REDIS_URL not set, push debouncing disabled")
        return PushDebouncer(redis=None)

    try:
        import redis

        client = redis.from_url(redis_url)
        # Test connection
        client.ping()
        logger.debug("Push debouncer connected to Redis")
        return PushDebouncer(redis=client)
    except Exception as e:
        logger.warning(
            "Failed to connect to Redis for debouncing",
            extra={"error": str(e)},
        )
        return PushDebouncer(redis=None)

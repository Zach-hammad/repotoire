"""Concurrency limiting for analysis tasks.

This module provides rate limiting per organization based on subscription tier:
- FREE: 1 concurrent analysis
- PRO: 3 concurrent analyses
- ENTERPRISE: 10 concurrent analyses

Uses Redis for distributed locking across worker nodes.
"""

from __future__ import annotations

import os
from functools import wraps
from typing import TYPE_CHECKING, Callable
from uuid import UUID

import redis

from repotoire.db.models import Organization, PlanTier, Repository
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from celery import Task

logger = get_logger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Concurrent analysis limits by subscription tier
TIER_LIMITS = {
    PlanTier.FREE: 1,
    PlanTier.PRO: 3,
    PlanTier.ENTERPRISE: 10,
}

# Default limit for unknown tiers
DEFAULT_LIMIT = 1


class ConcurrencyLimiter:
    """Limit concurrent analyses per organization.

    Uses Redis atomic operations to track and enforce limits across
    distributed worker nodes.

    Usage:
        limiter = ConcurrencyLimiter()
        if limiter.acquire(org_id, tier):
            try:
                # Do analysis work
                pass
            finally:
                limiter.release(org_id)
        else:
            # Queue is full, retry later
            raise self.retry(countdown=60)
    """

    def __init__(self) -> None:
        """Initialize the limiter with Redis connection."""
        self._redis: redis.Redis | None = None

    @property
    def redis(self) -> redis.Redis:
        """Lazy initialization of Redis connection."""
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL)
        return self._redis

    def acquire(self, org_id: UUID, tier: PlanTier) -> bool:
        """Try to acquire a slot for analysis.

        Uses atomic increment with limit check. If over limit,
        decrements and returns False.

        Args:
            org_id: Organization UUID.
            tier: Subscription tier for limit lookup.

        Returns:
            True if slot acquired, False if limit reached.
        """
        key = f"analysis:concurrent:{org_id}"
        limit = TIER_LIMITS.get(tier, DEFAULT_LIMIT)

        try:
            # Atomic increment with expiry
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, 3600)  # 1 hour expiry (safety net)
            result = pipe.execute()

            current = result[0]

            if current > limit:
                # Over limit, decrement and reject
                self.redis.decr(key)
                logger.info(
                    "Concurrency limit reached",
                    extra={"org_id": str(org_id), "tier": tier.value, "limit": limit, "current": current},
                )
                return False

            logger.debug(
                "Acquired analysis slot",
                extra={"org_id": str(org_id), "tier": tier.value, "slot": current, "limit": limit},
            )
            return True

        except redis.RedisError as e:
            # On Redis failure, allow the task (fail open)
            logger.warning(f"Redis error in acquire, allowing task: {e}")
            return True

    def release(self, org_id: UUID) -> None:
        """Release an analysis slot.

        Args:
            org_id: Organization UUID.
        """
        key = f"analysis:concurrent:{org_id}"

        try:
            # Ensure we don't go below 0
            current = self.redis.get(key)
            if current and int(current) > 0:
                self.redis.decr(key)
                logger.debug("Released analysis slot", extra={"org_id": str(org_id)})
        except redis.RedisError as e:
            logger.warning(f"Redis error in release: {e}")

    def get_current_count(self, org_id: UUID) -> int:
        """Get current concurrent analysis count for organization.

        Args:
            org_id: Organization UUID.

        Returns:
            Current count of concurrent analyses.
        """
        key = f"analysis:concurrent:{org_id}"

        try:
            count = self.redis.get(key)
            return int(count) if count else 0
        except redis.RedisError as e:
            logger.warning(f"Redis error in get_current_count: {e}")
            return 0

    def get_limit(self, tier: PlanTier) -> int:
        """Get concurrency limit for tier.

        Args:
            tier: Subscription tier.

        Returns:
            Maximum concurrent analyses allowed.
        """
        return TIER_LIMITS.get(tier, DEFAULT_LIMIT)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()
            self._redis = None


def with_concurrency_limit(func: Callable) -> Callable:
    """Decorator to enforce concurrency limits on tasks.

    Acquires a slot before running and releases it after completion
    or failure. If limit is reached, retries the task after 60 seconds.

    Usage:
        @celery_app.task(bind=True)
        @with_concurrency_limit
        def analyze_repository(self, analysis_run_id, repo_id, ...):
            ...
    """

    @wraps(func)
    def wrapper(self: "Task", analysis_run_id: str, repo_id: str, *args, **kwargs):
        limiter = ConcurrencyLimiter()

        try:
            # Get organization and tier
            with get_sync_session() as session:
                repo = session.get(Repository, UUID(repo_id))
                if not repo:
                    raise ValueError(f"Repository {repo_id} not found")

                org = repo.organization
                org_id = org.id
                tier = org.plan_tier

            # Try to acquire slot
            if not limiter.acquire(org_id, tier):
                # Queue is full, retry later
                logger.info(
                    "Retrying due to concurrency limit",
                    extra={"repo_id": repo_id, "org_id": str(org_id), "tier": tier.value},
                )
                raise self.retry(
                    countdown=60,
                    exc=Exception(
                        f"Concurrent analysis limit reached for organization {org_id}"
                    ),
                )

            try:
                return func(self, analysis_run_id, repo_id, *args, **kwargs)
            finally:
                limiter.release(org_id)

        finally:
            limiter.close()

    return wrapper


class RateLimiter:
    """Rate limiter for API requests per organization.

    Uses Redis sliding window for rate limiting.
    """

    def __init__(self, requests_per_minute: int = 60) -> None:
        """Initialize rate limiter.

        Args:
            requests_per_minute: Maximum requests allowed per minute.
        """
        self._redis: redis.Redis | None = None
        self.requests_per_minute = requests_per_minute
        self.window_seconds = 60

    @property
    def redis(self) -> redis.Redis:
        """Lazy initialization of Redis connection."""
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL)
        return self._redis

    def is_allowed(self, org_id: UUID) -> bool:
        """Check if request is allowed under rate limit.

        Args:
            org_id: Organization UUID.

        Returns:
            True if request is allowed, False if rate limited.
        """
        import time

        key = f"ratelimit:{org_id}"
        now = time.time()
        window_start = now - self.window_seconds

        try:
            pipe = self.redis.pipeline()
            # Remove old entries
            pipe.zremrangebyscore(key, 0, window_start)
            # Count current entries
            pipe.zcard(key)
            # Add new entry
            pipe.zadd(key, {str(now): now})
            # Set expiry
            pipe.expire(key, self.window_seconds * 2)
            results = pipe.execute()

            current_count = results[1]
            return current_count < self.requests_per_minute

        except redis.RedisError as e:
            logger.warning(f"Redis error in rate limiter: {e}")
            return True  # Fail open

    def get_remaining(self, org_id: UUID) -> int:
        """Get remaining requests in current window.

        Args:
            org_id: Organization UUID.

        Returns:
            Number of remaining requests.
        """
        import time

        key = f"ratelimit:{org_id}"
        window_start = time.time() - self.window_seconds

        try:
            # Remove old and count current
            self.redis.zremrangebyscore(key, 0, window_start)
            current = self.redis.zcard(key)
            return max(0, self.requests_per_minute - current)
        except redis.RedisError:
            return self.requests_per_minute

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()
            self._redis = None

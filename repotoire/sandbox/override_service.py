"""Service layer for quota override enforcement.

This module provides a service that bridges the QuotaOverrideRepository
to the quota enforcement system, with Redis caching for fast lookups.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional
from uuid import UUID

import redis.asyncio as aioredis

from repotoire.db.models.quota_override import QuotaOverrideType
from repotoire.db.repositories.quota_override import QuotaOverrideRepository
from repotoire.logging_config import get_logger
from repotoire.sandbox.quotas import SandboxQuota, get_quota_for_tier

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from repotoire.db.models import PlanTier

logger = get_logger(__name__)

# Cache configuration
CACHE_TTL_SECONDS = 300  # 5 minutes
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class QuotaOverrideService:
    """Service for checking quota overrides with caching.

    Provides efficient quota limit lookups that:
    - Check Redis cache first for fast response
    - Fall back to database if cache miss
    - Support graceful degradation if services unavailable

    Example:
        ```python
        service = QuotaOverrideService(db_session, redis_client)

        # Get effective limit for a quota type
        limit = await service.get_effective_limit(
            org_id=org_uuid,
            override_type=QuotaOverrideType.CONCURRENT_SESSIONS,
            tier_limit=10,
        )
        print(f"Effective limit: {limit}")
        ```
    """

    def __init__(
        self,
        db: "AsyncSession",
        redis: Optional["Redis"] = None,
        cache_ttl: int = CACHE_TTL_SECONDS,
    ):
        """Initialize the service.

        Args:
            db: Async database session
            redis: Optional async Redis client for caching
            cache_ttl: Cache TTL in seconds (default: 300)
        """
        self.db = db
        self.redis = redis
        self.cache_ttl = cache_ttl
        self._repo: Optional[QuotaOverrideRepository] = None

    @property
    def repo(self) -> QuotaOverrideRepository:
        """Get the repository, creating if needed."""
        if self._repo is None:
            self._repo = QuotaOverrideRepository(self.db, self.redis, self.cache_ttl)
        return self._repo

    async def get_effective_limit(
        self,
        org_id: UUID,
        override_type: QuotaOverrideType,
        tier_limit: int,
    ) -> int:
        """Get the effective limit for a quota type.

        Checks for active overrides and returns the override limit
        if one exists, otherwise returns the tier limit.

        Args:
            org_id: Organization UUID
            override_type: Type of quota to check
            tier_limit: The default tier limit to use if no override

        Returns:
            The effective limit (override if exists, else tier limit)
        """
        try:
            override = await self.repo.get_active_override(org_id, override_type)

            if override:
                logger.debug(
                    "Using quota override",
                    extra={
                        "org_id": str(org_id),
                        "type": override_type.value,
                        "tier_limit": tier_limit,
                        "override_limit": override.override_limit,
                    },
                )
                return override.override_limit

            return tier_limit

        except Exception as e:
            logger.warning(
                f"Error checking quota override, using tier limit: {e}",
                extra={
                    "org_id": str(org_id),
                    "type": override_type.value,
                    "tier_limit": tier_limit,
                },
            )
            return tier_limit

    async def get_effective_sandbox_quota(
        self,
        org_id: UUID,
        tier: "PlanTier",
    ) -> tuple[SandboxQuota, bool]:
        """Get effective SandboxQuota with all overrides applied.

        Args:
            org_id: Organization UUID
            tier: The organization's plan tier

        Returns:
            Tuple of (effective SandboxQuota, has_any_override)
        """
        base_quota = get_quota_for_tier(tier)
        has_override = False

        try:
            active_overrides = await self.repo.get_all_active_overrides(org_id)

            if not active_overrides:
                return base_quota, False

            # Build effective quota with overrides
            max_concurrent = base_quota.max_concurrent_sandboxes
            max_daily_minutes = base_quota.max_daily_sandbox_minutes
            max_monthly_minutes = base_quota.max_monthly_sandbox_minutes
            max_sandboxes_per_day = base_quota.max_sandboxes_per_day

            if QuotaOverrideType.CONCURRENT_SESSIONS in active_overrides:
                max_concurrent = active_overrides[
                    QuotaOverrideType.CONCURRENT_SESSIONS
                ].override_limit
                has_override = True

            if QuotaOverrideType.DAILY_SANDBOX_MINUTES in active_overrides:
                max_daily_minutes = active_overrides[
                    QuotaOverrideType.DAILY_SANDBOX_MINUTES
                ].override_limit
                has_override = True

            if QuotaOverrideType.MONTHLY_SANDBOX_MINUTES in active_overrides:
                max_monthly_minutes = active_overrides[
                    QuotaOverrideType.MONTHLY_SANDBOX_MINUTES
                ].override_limit
                has_override = True

            if QuotaOverrideType.SANDBOXES_PER_DAY in active_overrides:
                max_sandboxes_per_day = active_overrides[
                    QuotaOverrideType.SANDBOXES_PER_DAY
                ].override_limit
                has_override = True

            effective_quota = SandboxQuota(
                max_concurrent_sandboxes=max_concurrent,
                max_daily_sandbox_minutes=max_daily_minutes,
                max_monthly_sandbox_minutes=max_monthly_minutes,
                max_sandboxes_per_day=max_sandboxes_per_day,
                max_cost_per_day_usd=base_quota.max_cost_per_day_usd,
                max_cost_per_month_usd=base_quota.max_cost_per_month_usd,
            )

            return effective_quota, has_override

        except Exception as e:
            logger.warning(
                f"Error building effective quota, using base: {e}",
                extra={"org_id": str(org_id)},
            )
            return base_quota, False

    async def has_override(
        self,
        org_id: UUID,
        override_type: QuotaOverrideType,
    ) -> bool:
        """Check if an organization has an active override for a type.

        Args:
            org_id: Organization UUID
            override_type: Type of quota to check

        Returns:
            True if active override exists
        """
        try:
            override = await self.repo.get_active_override(org_id, override_type)
            return override is not None
        except Exception as e:
            logger.warning(f"Error checking for override: {e}")
            return False


# Global service instance helpers
_redis_client: Optional["Redis"] = None


async def get_redis_client() -> "Redis | None":
    """Get or create shared Redis client for override caching.

    Returns:
        Redis client or None if unavailable
    """
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        logger.debug("REDIS_URL not set, quota override caching disabled")
        return None

    try:
        _redis_client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await _redis_client.ping()
        logger.debug("Redis client connected for quota override caching")
        return _redis_client
    except Exception as e:
        logger.warning(f"Failed to connect to Redis: {e}")
        _redis_client = None
        return None


async def close_redis_client() -> None:
    """Close shared Redis client."""
    global _redis_client

    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.debug("Quota override Redis client closed")


async def get_override_service(db: "AsyncSession") -> QuotaOverrideService:
    """Create a QuotaOverrideService with Redis caching.

    Args:
        db: Async database session

    Returns:
        QuotaOverrideService instance
    """
    redis = await get_redis_client()
    return QuotaOverrideService(db, redis)

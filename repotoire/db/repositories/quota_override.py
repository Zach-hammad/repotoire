"""Repository for QuotaOverride database operations with Redis caching.

This module provides the repository pattern implementation for managing
quota overrides with Redis caching for fast lookups.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from repotoire.db.models.quota_override import QuotaOverride, QuotaOverrideType
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = get_logger(__name__)

# Cache configuration
CACHE_TTL_SECONDS = 300  # 5 minutes
CACHE_KEY_PREFIX = "quota:override:"


class QuotaOverrideNotFoundError(Exception):
    """Raised when a quota override is not found."""

    def __init__(self, override_id: UUID):
        self.override_id = override_id
        super().__init__(f"Quota override not found: {override_id}")


class OverrideAlreadyRevokedError(Exception):
    """Raised when attempting to revoke an already revoked override."""

    def __init__(self, override_id: UUID):
        self.override_id = override_id
        super().__init__(f"Quota override already revoked: {override_id}")


class QuotaOverrideRepository:
    """Repository for QuotaOverride operations with Redis caching.

    Provides CRUD operations for quota overrides with automatic cache
    invalidation and fast lookups for active overrides.
    """

    def __init__(
        self,
        db: AsyncSession,
        redis: Optional["Redis"] = None,
        cache_ttl: int = CACHE_TTL_SECONDS,
    ):
        """Initialize the repository.

        Args:
            db: Async database session
            redis: Optional async Redis client for caching
            cache_ttl: Cache TTL in seconds (default: 300)
        """
        self.db = db
        self.redis = redis
        self.cache_ttl = cache_ttl

    def _cache_key(self, org_id: UUID, override_type: QuotaOverrideType) -> str:
        """Generate cache key for an override lookup."""
        return f"{CACHE_KEY_PREFIX}{org_id}:{override_type.value}"

    async def _invalidate_cache(
        self,
        org_id: UUID,
        override_type: QuotaOverrideType,
    ) -> None:
        """Invalidate cache for an override."""
        if self.redis is None:
            return

        try:
            cache_key = self._cache_key(org_id, override_type)
            await self.redis.delete(cache_key)
            logger.debug(f"Invalidated cache for {cache_key}")
        except Exception as e:
            logger.warning(f"Failed to invalidate cache: {e}")

    async def _invalidate_all_org_cache(self, org_id: UUID) -> None:
        """Invalidate all cache entries for an organization."""
        if self.redis is None:
            return

        try:
            # Delete all override type caches for this org
            for override_type in QuotaOverrideType:
                cache_key = self._cache_key(org_id, override_type)
                await self.redis.delete(cache_key)
            logger.debug(f"Invalidated all caches for org {org_id}")
        except Exception as e:
            logger.warning(f"Failed to invalidate org caches: {e}")

    async def get_active_override(
        self,
        org_id: UUID,
        override_type: QuotaOverrideType,
    ) -> Optional[QuotaOverride]:
        """Get the currently active override for an org and type.

        Uses Redis cache for fast lookups with database fallback.

        Args:
            org_id: Organization ID
            override_type: Type of quota override

        Returns:
            Active QuotaOverride or None if no active override
        """
        # Check cache first
        if self.redis is not None:
            try:
                cache_key = self._cache_key(org_id, override_type)
                cached = await self.redis.get(cache_key)
                if cached is not None:
                    if cached == "NULL":
                        return None
                    # Parse cached override data
                    data = json.loads(cached)
                    # Re-fetch from DB to get proper ORM object
                    # (Cache stores serialized data for fast null checks)
                    override = await self.get_by_id(UUID(data["id"]))
                    if override and override.is_active:
                        return override
                    # Cache was stale, invalidate
                    await self.redis.delete(cache_key)
            except Exception as e:
                logger.warning(f"Cache read error: {e}")

        # Query database for active override
        result = await self.db.execute(
            select(QuotaOverride)
            .where(QuotaOverride.organization_id == org_id)
            .where(QuotaOverride.override_type == override_type)
            .where(QuotaOverride.revoked_at.is_(None))
            .where(
                or_(
                    QuotaOverride.expires_at.is_(None),
                    QuotaOverride.expires_at > func.now(),
                )
            )
            .order_by(QuotaOverride.created_at.desc())
            .limit(1)
        )
        override = result.scalar_one_or_none()

        # Cache result (including null)
        if self.redis is not None:
            try:
                cache_key = self._cache_key(org_id, override_type)
                if override:
                    cache_data = json.dumps(
                        {
                            "id": str(override.id),
                            "override_limit": override.override_limit,
                        }
                    )
                    await self.redis.setex(cache_key, self.cache_ttl, cache_data)
                else:
                    await self.redis.setex(cache_key, self.cache_ttl, "NULL")
            except Exception as e:
                logger.warning(f"Cache write error: {e}")

        return override

    async def get_all_active_overrides(
        self,
        org_id: UUID,
    ) -> dict[QuotaOverrideType, QuotaOverride]:
        """Get all active overrides for an organization.

        Args:
            org_id: Organization ID

        Returns:
            Dict mapping override type to active override
        """
        result = await self.db.execute(
            select(QuotaOverride)
            .where(QuotaOverride.organization_id == org_id)
            .where(QuotaOverride.revoked_at.is_(None))
            .where(
                or_(
                    QuotaOverride.expires_at.is_(None),
                    QuotaOverride.expires_at > func.now(),
                )
            )
            .order_by(QuotaOverride.created_at.desc())
        )
        overrides = result.scalars().all()

        # Return latest override for each type
        active: dict[QuotaOverrideType, QuotaOverride] = {}
        for override in overrides:
            if override.override_type not in active:
                active[override.override_type] = override

        return active

    async def create(
        self,
        organization_id: UUID,
        override_type: QuotaOverrideType,
        original_limit: int,
        override_limit: int,
        reason: str,
        created_by_id: UUID,
        expires_at: Optional[datetime] = None,
    ) -> QuotaOverride:
        """Create a new quota override.

        Automatically revokes any existing active override of the same type.

        Args:
            organization_id: Organization receiving the override
            override_type: Type of quota being overridden
            original_limit: What the tier limit was
            override_limit: New limit granted
            reason: Why override was granted (audit trail)
            created_by_id: Admin creating the override
            expires_at: Optional expiration datetime

        Returns:
            The created QuotaOverride instance
        """
        # Revoke any existing active override of same type
        existing = await self.get_active_override(organization_id, override_type)
        if existing:
            await self._revoke_existing(
                existing,
                created_by_id,
                "Superseded by new override",
            )

        # Create new override
        override = QuotaOverride(
            organization_id=organization_id,
            override_type=override_type,
            original_limit=original_limit,
            override_limit=override_limit,
            reason=reason,
            created_by_id=created_by_id,
            expires_at=expires_at,
        )
        self.db.add(override)
        await self.db.commit()
        await self.db.refresh(override)

        # Invalidate cache
        await self._invalidate_cache(organization_id, override_type)

        logger.info(
            "Quota override created",
            extra={
                "override_id": str(override.id),
                "org_id": str(organization_id),
                "type": override_type.value,
                "original": original_limit,
                "new": override_limit,
                "created_by": str(created_by_id),
            },
        )

        return override

    async def _revoke_existing(
        self,
        override: QuotaOverride,
        revoked_by_id: UUID,
        reason: str,
    ) -> None:
        """Internal method to revoke an override without cache invalidation."""
        override.revoked_at = datetime.utcnow()
        override.revoked_by_id = revoked_by_id
        override.revoke_reason = reason
        await self.db.commit()

    async def get_by_id(
        self,
        override_id: UUID,
        include_relationships: bool = False,
    ) -> Optional[QuotaOverride]:
        """Get a quota override by ID.

        Args:
            override_id: The override ID
            include_relationships: Whether to eagerly load relationships

        Returns:
            The QuotaOverride instance or None if not found
        """
        query = select(QuotaOverride).where(QuotaOverride.id == override_id)
        if include_relationships:
            query = query.options(
                selectinload(QuotaOverride.organization),
                selectinload(QuotaOverride.created_by),
                selectinload(QuotaOverride.revoked_by),
            )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_id_or_raise(
        self,
        override_id: UUID,
        include_relationships: bool = False,
    ) -> QuotaOverride:
        """Get a quota override by ID or raise an error.

        Args:
            override_id: The override ID
            include_relationships: Whether to eagerly load relationships

        Returns:
            The QuotaOverride instance

        Raises:
            QuotaOverrideNotFoundError: If the override is not found
        """
        override = await self.get_by_id(override_id, include_relationships)
        if override is None:
            raise QuotaOverrideNotFoundError(override_id)
        return override

    async def revoke(
        self,
        override_id: UUID,
        revoked_by_id: UUID,
        reason: str,
    ) -> QuotaOverride:
        """Revoke a quota override.

        Args:
            override_id: Override to revoke
            revoked_by_id: Admin revoking the override
            reason: Why the override is being revoked

        Returns:
            The revoked QuotaOverride instance

        Raises:
            QuotaOverrideNotFoundError: If override not found
            OverrideAlreadyRevokedError: If already revoked
        """
        override = await self.get_by_id_or_raise(override_id)

        if override.revoked_at is not None:
            raise OverrideAlreadyRevokedError(override_id)

        override.revoked_at = datetime.utcnow()
        override.revoked_by_id = revoked_by_id
        override.revoke_reason = reason

        await self.db.commit()
        await self.db.refresh(override)

        # Invalidate cache
        await self._invalidate_cache(override.organization_id, override.override_type)

        logger.info(
            "Quota override revoked",
            extra={
                "override_id": str(override_id),
                "org_id": str(override.organization_id),
                "revoked_by": str(revoked_by_id),
                "reason": reason,
            },
        )

        return override

    async def search(
        self,
        organization_id: Optional[UUID] = None,
        created_by_id: Optional[UUID] = None,
        override_type: Optional[QuotaOverrideType] = None,
        include_revoked: bool = False,
        include_expired: bool = False,
        include_relationships: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[QuotaOverride], int]:
        """Search quota overrides with filters and pagination.

        Args:
            organization_id: Filter by organization
            created_by_id: Filter by admin who created
            override_type: Filter by override type
            include_revoked: Include revoked overrides
            include_expired: Include expired overrides
            include_relationships: Eagerly load relationships
            limit: Maximum results
            offset: Number to skip

        Returns:
            Tuple of (list of overrides, total count)
        """
        # Base query
        query = select(QuotaOverride)
        count_query = select(func.count()).select_from(QuotaOverride)

        # Apply filters
        if organization_id:
            query = query.where(QuotaOverride.organization_id == organization_id)
            count_query = count_query.where(
                QuotaOverride.organization_id == organization_id
            )

        if created_by_id:
            query = query.where(QuotaOverride.created_by_id == created_by_id)
            count_query = count_query.where(
                QuotaOverride.created_by_id == created_by_id
            )

        if override_type:
            query = query.where(QuotaOverride.override_type == override_type)
            count_query = count_query.where(
                QuotaOverride.override_type == override_type
            )

        if not include_revoked:
            query = query.where(QuotaOverride.revoked_at.is_(None))
            count_query = count_query.where(QuotaOverride.revoked_at.is_(None))

        if not include_expired:
            query = query.where(
                or_(
                    QuotaOverride.expires_at.is_(None),
                    QuotaOverride.expires_at > func.now(),
                )
            )
            count_query = count_query.where(
                or_(
                    QuotaOverride.expires_at.is_(None),
                    QuotaOverride.expires_at > func.now(),
                )
            )

        # Get total count
        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        # Apply sorting and pagination
        query = (
            query.order_by(QuotaOverride.created_at.desc()).limit(limit).offset(offset)
        )

        # Load relationships if requested
        if include_relationships:
            query = query.options(
                selectinload(QuotaOverride.organization),
                selectinload(QuotaOverride.created_by),
                selectinload(QuotaOverride.revoked_by),
            )

        result = await self.db.execute(query)
        return result.scalars().all(), total

    async def get_audit_history(
        self,
        organization_id: UUID,
        limit: int = 100,
    ) -> Sequence[QuotaOverride]:
        """Get full audit history for an organization.

        Includes all overrides (active, revoked, expired) for audit purposes.

        Args:
            organization_id: Organization ID
            limit: Maximum results

        Returns:
            List of all overrides ordered by creation date
        """
        result = await self.db.execute(
            select(QuotaOverride)
            .options(
                selectinload(QuotaOverride.created_by),
                selectinload(QuotaOverride.revoked_by),
            )
            .where(QuotaOverride.organization_id == organization_id)
            .order_by(QuotaOverride.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def cleanup_expired(self) -> int:
        """Mark expired overrides as revoked for cleanup.

        This is for maintenance - expired overrides are already inactive
        but this marks them with revoke_reason for clarity.

        Returns:
            Number of overrides marked as expired
        """
        now = datetime.utcnow()
        result = await self.db.execute(
            select(QuotaOverride)
            .where(QuotaOverride.revoked_at.is_(None))
            .where(QuotaOverride.expires_at.isnot(None))
            .where(QuotaOverride.expires_at <= now)
        )
        expired = result.scalars().all()

        count = 0
        for override in expired:
            override.revoked_at = now
            override.revoke_reason = "Expired"
            await self._invalidate_cache(
                override.organization_id, override.override_type
            )
            count += 1

        if count > 0:
            await self.db.commit()
            logger.info(f"Marked {count} expired overrides as revoked")

        return count

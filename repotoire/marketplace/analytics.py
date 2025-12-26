"""Marketplace analytics tracking and aggregation service.

This module provides the AnalyticsTracker class for tracking marketplace events
and updating aggregated statistics.

Usage:
    from repotoire.marketplace.analytics import AnalyticsTracker, EventType

    tracker = AnalyticsTracker(db_session)
    await tracker.track_event(
        asset_id=asset_uuid,
        event_type=EventType.INSTALL,
        user_id="user_xxx",
        cli_version="0.5.0",
        os_platform="darwin",
        source="cli",
    )

    # Get creator stats
    stats = await tracker.get_creator_stats(publisher_id)
    asset_stats = await tracker.get_asset_stats(asset_id)
    trends = await tracker.get_asset_trends(asset_id, days=30)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from repotoire.db.models.marketplace import (
    AssetEvent,
    AssetStats,
    AssetStatsDaily,
    EventType,
    MarketplaceAsset,
    MarketplacePublisher,
    PublisherStats,
)
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


# =============================================================================
# Data Classes for Analytics Responses
# =============================================================================


@dataclass
class EventData:
    """Data for tracking an event."""

    asset_id: UUID
    event_type: EventType
    user_id: Optional[str] = None
    asset_version_id: Optional[UUID] = None
    cli_version: Optional[str] = None
    os_platform: Optional[str] = None
    source: Optional[str] = "api"
    metadata: Optional[dict] = None


@dataclass
class AssetStatsResponse:
    """Response model for asset statistics."""

    asset_id: UUID
    total_downloads: int = 0
    total_installs: int = 0
    total_uninstalls: int = 0
    total_updates: int = 0
    active_installs: int = 0
    rating_avg: Optional[Decimal] = None
    rating_count: int = 0
    total_revenue_cents: int = 0
    total_purchases: int = 0
    downloads_7d: int = 0
    downloads_30d: int = 0
    installs_7d: int = 0
    installs_30d: int = 0


@dataclass
class DailyStats:
    """Daily statistics for a single day."""

    date: date
    downloads: int = 0
    installs: int = 0
    uninstalls: int = 0
    updates: int = 0
    revenue_cents: int = 0
    unique_users: int = 0


@dataclass
class AssetTrends:
    """Trend data for an asset over a time period."""

    asset_id: UUID
    period_days: int
    daily_stats: list[DailyStats] = field(default_factory=list)
    total_downloads: int = 0
    total_installs: int = 0
    total_uninstalls: int = 0
    total_revenue_cents: int = 0
    avg_daily_downloads: float = 0.0
    avg_daily_installs: float = 0.0


@dataclass
class CreatorStatsResponse:
    """Response model for creator/publisher statistics."""

    publisher_id: UUID
    total_assets: int = 0
    total_downloads: int = 0
    total_installs: int = 0
    total_active_installs: int = 0
    total_revenue_cents: int = 0
    avg_rating: Optional[Decimal] = None
    total_reviews: int = 0
    downloads_7d: int = 0
    downloads_30d: int = 0
    # Per-asset breakdown
    assets: list[AssetStatsResponse] = field(default_factory=list)


@dataclass
class PlatformStats:
    """Platform-wide analytics for admin dashboard."""

    total_assets: int = 0
    total_publishers: int = 0
    total_downloads: int = 0
    total_installs: int = 0
    total_active_installs: int = 0
    total_revenue_cents: int = 0
    downloads_7d: int = 0
    downloads_30d: int = 0
    installs_7d: int = 0
    installs_30d: int = 0
    # Top assets by various metrics
    top_by_downloads: list[dict] = field(default_factory=list)
    top_by_installs: list[dict] = field(default_factory=list)
    top_by_revenue: list[dict] = field(default_factory=list)


# =============================================================================
# Analytics Tracker Class
# =============================================================================


class AnalyticsTracker:
    """Service for tracking marketplace analytics events.

    This class handles:
    - Recording individual events (downloads, installs, etc.)
    - Updating aggregated statistics in real-time
    - Querying statistics for dashboards
    """

    def __init__(self, db: "AsyncSession"):
        """Initialize the analytics tracker.

        Args:
            db: SQLAlchemy async session
        """
        self.db = db

    async def track_event(self, data: EventData) -> AssetEvent:
        """Track an analytics event and update aggregated stats.

        Args:
            data: Event data to track

        Returns:
            The created AssetEvent record
        """
        # Create the event record
        event = AssetEvent(
            id=uuid4(),
            asset_id=data.asset_id,
            asset_version_id=data.asset_version_id,
            user_id=data.user_id,
            event_type=data.event_type.value,
            cli_version=data.cli_version,
            os_platform=data.os_platform,
            source=data.source,
            event_metadata=data.metadata,
        )

        self.db.add(event)

        # Update aggregated stats
        await self._update_asset_stats(data.asset_id, data.event_type)

        await self.db.flush()

        logger.debug(
            "Tracked event",
            asset_id=str(data.asset_id),
            event_type=data.event_type.value,
            user_id=data.user_id,
        )

        return event

    async def _update_asset_stats(self, asset_id: UUID, event_type: EventType) -> None:
        """Update aggregated stats for an asset based on event type.

        Uses upsert to handle race conditions.

        Args:
            asset_id: The asset UUID
            event_type: Type of event that occurred
        """
        # Build the update dict based on event type
        updates = {}
        if event_type == EventType.DOWNLOAD:
            updates = {"total_downloads": AssetStats.total_downloads + 1}
        elif event_type == EventType.INSTALL:
            updates = {
                "total_installs": AssetStats.total_installs + 1,
                "active_installs": AssetStats.active_installs + 1,
            }
        elif event_type == EventType.UNINSTALL:
            updates = {
                "total_uninstalls": AssetStats.total_uninstalls + 1,
                "active_installs": AssetStats.active_installs - 1,
            }
        elif event_type == EventType.UPDATE:
            updates = {"total_updates": AssetStats.total_updates + 1}

        if not updates:
            return

        # Try to update existing stats
        stmt = (
            update(AssetStats)
            .where(AssetStats.asset_id == asset_id)
            .values(**updates)
            .returning(AssetStats.id)
        )
        result = await self.db.execute(stmt)

        # If no row was updated, insert a new one
        if result.rowcount == 0:
            # Create new stats row with the event counted
            initial_values = {
                "id": uuid4(),
                "asset_id": asset_id,
                "total_downloads": 1 if event_type == EventType.DOWNLOAD else 0,
                "total_installs": 1 if event_type == EventType.INSTALL else 0,
                "total_uninstalls": 1 if event_type == EventType.UNINSTALL else 0,
                "total_updates": 1 if event_type == EventType.UPDATE else 0,
                "active_installs": 1 if event_type == EventType.INSTALL else 0,
            }
            stats = AssetStats(**initial_values)
            self.db.add(stats)

    async def get_asset_stats(self, asset_id: UUID) -> AssetStatsResponse:
        """Get aggregated statistics for an asset.

        Args:
            asset_id: The asset UUID

        Returns:
            AssetStatsResponse with all stats
        """
        stmt = select(AssetStats).where(AssetStats.asset_id == asset_id)
        result = await self.db.execute(stmt)
        stats = result.scalar_one_or_none()

        if not stats:
            return AssetStatsResponse(asset_id=asset_id)

        return AssetStatsResponse(
            asset_id=asset_id,
            total_downloads=stats.total_downloads,
            total_installs=stats.total_installs,
            total_uninstalls=stats.total_uninstalls,
            total_updates=stats.total_updates,
            active_installs=stats.active_installs,
            rating_avg=stats.rating_avg,
            rating_count=stats.rating_count,
            total_revenue_cents=stats.total_revenue_cents,
            total_purchases=stats.total_purchases,
            downloads_7d=stats.downloads_7d,
            downloads_30d=stats.downloads_30d,
            installs_7d=stats.installs_7d,
            installs_30d=stats.installs_30d,
        )

    async def get_asset_trends(
        self, asset_id: UUID, days: int = 30
    ) -> AssetTrends:
        """Get daily trend data for an asset.

        Args:
            asset_id: The asset UUID
            days: Number of days to retrieve (default 30)

        Returns:
            AssetTrends with daily breakdown
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        # Query daily stats
        stmt = (
            select(AssetStatsDaily)
            .where(
                and_(
                    AssetStatsDaily.asset_id == asset_id,
                    func.date(AssetStatsDaily.date) >= start_date,
                    func.date(AssetStatsDaily.date) <= end_date,
                )
            )
            .order_by(AssetStatsDaily.date)
        )
        result = await self.db.execute(stmt)
        daily_records = result.scalars().all()

        # Build daily stats list
        daily_stats = []
        total_downloads = 0
        total_installs = 0
        total_uninstalls = 0
        total_revenue = 0

        for record in daily_records:
            day_stats = DailyStats(
                date=record.date.date() if isinstance(record.date, datetime) else record.date,
                downloads=record.downloads,
                installs=record.installs,
                uninstalls=record.uninstalls,
                updates=record.updates,
                revenue_cents=record.revenue_cents,
                unique_users=record.unique_users,
            )
            daily_stats.append(day_stats)
            total_downloads += record.downloads
            total_installs += record.installs
            total_uninstalls += record.uninstalls
            total_revenue += record.revenue_cents

        return AssetTrends(
            asset_id=asset_id,
            period_days=days,
            daily_stats=daily_stats,
            total_downloads=total_downloads,
            total_installs=total_installs,
            total_uninstalls=total_uninstalls,
            total_revenue_cents=total_revenue,
            avg_daily_downloads=total_downloads / days if days > 0 else 0,
            avg_daily_installs=total_installs / days if days > 0 else 0,
        )

    async def get_creator_stats(self, publisher_id: UUID) -> CreatorStatsResponse:
        """Get aggregated statistics for a creator/publisher.

        Args:
            publisher_id: The publisher UUID

        Returns:
            CreatorStatsResponse with publisher-wide and per-asset stats
        """
        # Get publisher stats
        stmt = select(PublisherStats).where(PublisherStats.publisher_id == publisher_id)
        result = await self.db.execute(stmt)
        pub_stats = result.scalar_one_or_none()

        response = CreatorStatsResponse(
            publisher_id=publisher_id,
            total_assets=pub_stats.total_assets if pub_stats else 0,
            total_downloads=pub_stats.total_downloads if pub_stats else 0,
            total_installs=pub_stats.total_installs if pub_stats else 0,
            total_active_installs=pub_stats.total_active_installs if pub_stats else 0,
            total_revenue_cents=pub_stats.total_revenue_cents if pub_stats else 0,
            avg_rating=pub_stats.avg_rating if pub_stats else None,
            total_reviews=pub_stats.total_reviews if pub_stats else 0,
            downloads_7d=pub_stats.downloads_7d if pub_stats else 0,
            downloads_30d=pub_stats.downloads_30d if pub_stats else 0,
        )

        # Get per-asset stats
        stmt = (
            select(AssetStats, MarketplaceAsset.name, MarketplaceAsset.slug)
            .join(MarketplaceAsset, AssetStats.asset_id == MarketplaceAsset.id)
            .where(MarketplaceAsset.publisher_id == publisher_id)
            .order_by(AssetStats.total_downloads.desc())
        )
        result = await self.db.execute(stmt)

        for stats, name, slug in result:
            asset_stats = AssetStatsResponse(
                asset_id=stats.asset_id,
                total_downloads=stats.total_downloads,
                total_installs=stats.total_installs,
                total_uninstalls=stats.total_uninstalls,
                total_updates=stats.total_updates,
                active_installs=stats.active_installs,
                rating_avg=stats.rating_avg,
                rating_count=stats.rating_count,
                total_revenue_cents=stats.total_revenue_cents,
                total_purchases=stats.total_purchases,
                downloads_7d=stats.downloads_7d,
                downloads_30d=stats.downloads_30d,
                installs_7d=stats.installs_7d,
                installs_30d=stats.installs_30d,
            )
            response.assets.append(asset_stats)

        return response

    async def get_platform_stats(self, limit: int = 10) -> PlatformStats:
        """Get platform-wide analytics for admin dashboard.

        Args:
            limit: Number of top assets to return in each category

        Returns:
            PlatformStats with platform-wide metrics
        """
        # Get totals
        total_assets = await self.db.scalar(
            select(func.count()).select_from(MarketplaceAsset)
        )
        total_publishers = await self.db.scalar(
            select(func.count()).select_from(MarketplacePublisher)
        )

        # Sum all stats
        stats_totals = await self.db.execute(
            select(
                func.sum(AssetStats.total_downloads).label("downloads"),
                func.sum(AssetStats.total_installs).label("installs"),
                func.sum(AssetStats.active_installs).label("active"),
                func.sum(AssetStats.total_revenue_cents).label("revenue"),
                func.sum(AssetStats.downloads_7d).label("d7"),
                func.sum(AssetStats.downloads_30d).label("d30"),
                func.sum(AssetStats.installs_7d).label("i7"),
                func.sum(AssetStats.installs_30d).label("i30"),
            )
        )
        totals = stats_totals.one()

        response = PlatformStats(
            total_assets=total_assets or 0,
            total_publishers=total_publishers or 0,
            total_downloads=totals.downloads or 0,
            total_installs=totals.installs or 0,
            total_active_installs=totals.active or 0,
            total_revenue_cents=totals.revenue or 0,
            downloads_7d=totals.d7 or 0,
            downloads_30d=totals.d30 or 0,
            installs_7d=totals.i7 or 0,
            installs_30d=totals.i30 or 0,
        )

        # Top assets by downloads
        top_downloads = await self.db.execute(
            select(
                MarketplaceAsset.id,
                MarketplaceAsset.name,
                MarketplaceAsset.slug,
                MarketplacePublisher.slug.label("publisher_slug"),
                AssetStats.total_downloads,
            )
            .join(AssetStats, AssetStats.asset_id == MarketplaceAsset.id)
            .join(MarketplacePublisher, MarketplaceAsset.publisher_id == MarketplacePublisher.id)
            .order_by(AssetStats.total_downloads.desc())
            .limit(limit)
        )
        response.top_by_downloads = [
            {
                "id": str(row.id),
                "name": row.name,
                "slug": row.slug,
                "publisher_slug": row.publisher_slug,
                "total_downloads": row.total_downloads,
            }
            for row in top_downloads
        ]

        # Top assets by active installs
        top_installs = await self.db.execute(
            select(
                MarketplaceAsset.id,
                MarketplaceAsset.name,
                MarketplaceAsset.slug,
                MarketplacePublisher.slug.label("publisher_slug"),
                AssetStats.active_installs,
            )
            .join(AssetStats, AssetStats.asset_id == MarketplaceAsset.id)
            .join(MarketplacePublisher, MarketplaceAsset.publisher_id == MarketplacePublisher.id)
            .order_by(AssetStats.active_installs.desc())
            .limit(limit)
        )
        response.top_by_installs = [
            {
                "id": str(row.id),
                "name": row.name,
                "slug": row.slug,
                "publisher_slug": row.publisher_slug,
                "active_installs": row.active_installs,
            }
            for row in top_installs
        ]

        # Top assets by revenue
        top_revenue = await self.db.execute(
            select(
                MarketplaceAsset.id,
                MarketplaceAsset.name,
                MarketplaceAsset.slug,
                MarketplacePublisher.slug.label("publisher_slug"),
                AssetStats.total_revenue_cents,
            )
            .join(AssetStats, AssetStats.asset_id == MarketplaceAsset.id)
            .join(MarketplacePublisher, MarketplaceAsset.publisher_id == MarketplacePublisher.id)
            .where(AssetStats.total_revenue_cents > 0)
            .order_by(AssetStats.total_revenue_cents.desc())
            .limit(limit)
        )
        response.top_by_revenue = [
            {
                "id": str(row.id),
                "name": row.name,
                "slug": row.slug,
                "publisher_slug": row.publisher_slug,
                "total_revenue_cents": row.total_revenue_cents,
            }
            for row in top_revenue
        ]

        return response


# =============================================================================
# Background Job Functions
# =============================================================================


async def aggregate_daily_stats(db: "AsyncSession", target_date: Optional[date] = None) -> int:
    """Aggregate events into daily stats snapshots.

    Should be run nightly via cron/celery beat.

    Args:
        db: SQLAlchemy async session
        target_date: Date to aggregate (default: yesterday)

    Returns:
        Number of assets processed
    """
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    logger.info("Aggregating daily stats", date=str(target_date))

    # Get all assets
    assets_result = await db.execute(select(MarketplaceAsset.id))
    asset_ids = [row[0] for row in assets_result]

    processed = 0

    for asset_id in asset_ids:
        # Count events for the day
        start_dt = datetime.combine(target_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time()).replace(
            tzinfo=timezone.utc
        )

        # Get event counts
        events_result = await db.execute(
            select(
                AssetEvent.event_type,
                func.count().label("count"),
            )
            .where(
                and_(
                    AssetEvent.asset_id == asset_id,
                    AssetEvent.created_at >= start_dt,
                    AssetEvent.created_at < end_dt,
                )
            )
            .group_by(AssetEvent.event_type)
        )

        downloads = 0
        installs = 0
        uninstalls = 0
        updates = 0

        for row in events_result:
            if row.event_type == EventType.DOWNLOAD.value:
                downloads = row.count
            elif row.event_type == EventType.INSTALL.value:
                installs = row.count
            elif row.event_type == EventType.UNINSTALL.value:
                uninstalls = row.count
            elif row.event_type == EventType.UPDATE.value:
                updates = row.count

        # Get unique users
        unique_users_result = await db.scalar(
            select(func.count(func.distinct(AssetEvent.user_id))).where(
                and_(
                    AssetEvent.asset_id == asset_id,
                    AssetEvent.created_at >= start_dt,
                    AssetEvent.created_at < end_dt,
                    AssetEvent.user_id.isnot(None),
                )
            )
        )

        # Get current stats for cumulative values
        stats_result = await db.execute(
            select(AssetStats).where(AssetStats.asset_id == asset_id)
        )
        stats = stats_result.scalar_one_or_none()

        cumulative_downloads = stats.total_downloads if stats else downloads
        cumulative_installs = stats.total_installs if stats else installs
        active = stats.active_installs if stats else (installs - uninstalls)

        # Upsert daily stats
        stmt = pg_insert(AssetStatsDaily).values(
            id=uuid4(),
            asset_id=asset_id,
            date=target_date,
            downloads=downloads,
            installs=installs,
            uninstalls=uninstalls,
            updates=updates,
            cumulative_downloads=cumulative_downloads,
            cumulative_installs=cumulative_installs,
            active_installs=active,
            revenue_cents=0,  # TODO: Calculate from purchases
            purchases=0,
            unique_users=unique_users_result or 0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["asset_id", "date"],
            set_={
                "downloads": downloads,
                "installs": installs,
                "uninstalls": uninstalls,
                "updates": updates,
                "cumulative_downloads": cumulative_downloads,
                "cumulative_installs": cumulative_installs,
                "active_installs": active,
                "unique_users": unique_users_result or 0,
            },
        )
        await db.execute(stmt)
        processed += 1

    await db.commit()
    logger.info("Daily stats aggregation complete", assets_processed=processed)
    return processed


async def update_rolling_stats(db: "AsyncSession") -> int:
    """Update 7-day and 30-day rolling windows for all assets.

    Should be run daily via cron/celery beat.

    Args:
        db: SQLAlchemy async session

    Returns:
        Number of assets updated
    """
    logger.info("Updating rolling stats")

    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)

    # Get all asset IDs
    assets_result = await db.execute(select(MarketplaceAsset.id))
    asset_ids = [row[0] for row in assets_result]

    updated = 0

    for asset_id in asset_ids:
        # Calculate 7-day stats
        d7_result = await db.execute(
            select(
                func.count().filter(AssetEvent.event_type == EventType.DOWNLOAD.value).label("d"),
                func.count().filter(AssetEvent.event_type == EventType.INSTALL.value).label("i"),
            ).where(
                and_(
                    AssetEvent.asset_id == asset_id,
                    AssetEvent.created_at >= seven_days_ago,
                )
            )
        )
        d7 = d7_result.one()

        # Calculate 30-day stats
        d30_result = await db.execute(
            select(
                func.count().filter(AssetEvent.event_type == EventType.DOWNLOAD.value).label("d"),
                func.count().filter(AssetEvent.event_type == EventType.INSTALL.value).label("i"),
            ).where(
                and_(
                    AssetEvent.asset_id == asset_id,
                    AssetEvent.created_at >= thirty_days_ago,
                )
            )
        )
        d30 = d30_result.one()

        # Update stats
        await db.execute(
            update(AssetStats)
            .where(AssetStats.asset_id == asset_id)
            .values(
                downloads_7d=d7.d or 0,
                installs_7d=d7.i or 0,
                downloads_30d=d30.d or 0,
                installs_30d=d30.i or 0,
            )
        )
        updated += 1

    await db.commit()
    logger.info("Rolling stats update complete", assets_updated=updated)
    return updated


async def update_publisher_stats(db: "AsyncSession") -> int:
    """Recalculate aggregated stats for all publishers.

    Should be run daily via cron/celery beat.

    Args:
        db: SQLAlchemy async session

    Returns:
        Number of publishers updated
    """
    logger.info("Updating publisher stats")

    # Get all publishers
    publishers_result = await db.execute(select(MarketplacePublisher.id))
    publisher_ids = [row[0] for row in publishers_result]

    updated = 0

    for publisher_id in publisher_ids:
        # Aggregate stats from all assets
        stats_result = await db.execute(
            select(
                func.count().label("asset_count"),
                func.sum(AssetStats.total_downloads).label("downloads"),
                func.sum(AssetStats.total_installs).label("installs"),
                func.sum(AssetStats.active_installs).label("active"),
                func.sum(AssetStats.total_revenue_cents).label("revenue"),
                func.avg(AssetStats.rating_avg).label("avg_rating"),
                func.sum(AssetStats.rating_count).label("reviews"),
                func.sum(AssetStats.downloads_7d).label("d7"),
                func.sum(AssetStats.downloads_30d).label("d30"),
            )
            .select_from(AssetStats)
            .join(MarketplaceAsset, AssetStats.asset_id == MarketplaceAsset.id)
            .where(MarketplaceAsset.publisher_id == publisher_id)
        )
        totals = stats_result.one()

        # Upsert publisher stats
        stmt = pg_insert(PublisherStats).values(
            id=uuid4(),
            publisher_id=publisher_id,
            total_assets=totals.asset_count or 0,
            total_downloads=totals.downloads or 0,
            total_installs=totals.installs or 0,
            total_active_installs=totals.active or 0,
            total_revenue_cents=totals.revenue or 0,
            avg_rating=totals.avg_rating,
            total_reviews=totals.reviews or 0,
            downloads_7d=totals.d7 or 0,
            downloads_30d=totals.d30 or 0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["publisher_id"],
            set_={
                "total_assets": totals.asset_count or 0,
                "total_downloads": totals.downloads or 0,
                "total_installs": totals.installs or 0,
                "total_active_installs": totals.active or 0,
                "total_revenue_cents": totals.revenue or 0,
                "avg_rating": totals.avg_rating,
                "total_reviews": totals.reviews or 0,
                "downloads_7d": totals.d7 or 0,
                "downloads_30d": totals.d30 or 0,
            },
        )
        await db.execute(stmt)
        updated += 1

    await db.commit()
    logger.info("Publisher stats update complete", publishers_updated=updated)
    return updated


async def cleanup_old_events(db: "AsyncSession", days_to_keep: int = 90) -> int:
    """Delete old events to manage table size.

    Daily snapshots are retained, but raw events can be pruned.

    Args:
        db: SQLAlchemy async session
        days_to_keep: Number of days of events to retain

    Returns:
        Number of events deleted
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_to_keep)

    logger.info("Cleaning up old events", cutoff_date=str(cutoff.date()))

    result = await db.execute(
        delete(AssetEvent).where(AssetEvent.created_at < cutoff)
    )

    await db.commit()
    deleted = result.rowcount

    logger.info("Event cleanup complete", events_deleted=deleted)
    return deleted

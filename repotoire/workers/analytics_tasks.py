"""Celery tasks for marketplace analytics.

This module contains scheduled tasks for:
- Daily stats aggregation (nightly)
- Rolling window updates (daily)
- Publisher stats recalculation (daily)
- Event cleanup (weekly)
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from repotoire.db.session import get_async_session
from repotoire.logging_config import get_logger
from repotoire.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="repotoire.workers.analytics_tasks.aggregate_daily_stats",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def aggregate_daily_stats(self, target_date: str | None = None) -> dict[str, Any]:
    """Aggregate events into daily stats snapshots.

    Run nightly at 1 AM UTC to aggregate yesterday's events.

    Args:
        target_date: ISO date string (YYYY-MM-DD) to aggregate. Defaults to yesterday.

    Returns:
        dict with status and assets_processed count.
    """
    import asyncio
    from repotoire.marketplace.analytics import aggregate_daily_stats as do_aggregate

    # Parse target date
    if target_date:
        dt = date.fromisoformat(target_date)
    else:
        dt = date.today() - timedelta(days=1)

    logger.info("Starting daily stats aggregation", date=str(dt))

    async def run():
        async with get_async_session() as db:
            return await do_aggregate(db, target_date=dt)

    processed = asyncio.run(run())

    logger.info("Daily stats aggregation complete", date=str(dt), assets_processed=processed)

    return {
        "status": "completed",
        "date": str(dt),
        "assets_processed": processed,
    }


@celery_app.task(
    name="repotoire.workers.analytics_tasks.update_rolling_stats",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def update_rolling_stats(self) -> dict[str, Any]:
    """Update 7-day and 30-day rolling windows for all assets.

    Run daily at 2 AM UTC after daily aggregation completes.

    Returns:
        dict with status and assets_updated count.
    """
    import asyncio
    from repotoire.marketplace.analytics import update_rolling_stats as do_update

    logger.info("Starting rolling stats update")

    async def run():
        async with get_async_session() as db:
            return await do_update(db)

    updated = asyncio.run(run())

    logger.info("Rolling stats update complete", assets_updated=updated)

    return {
        "status": "completed",
        "assets_updated": updated,
    }


@celery_app.task(
    name="repotoire.workers.analytics_tasks.update_publisher_stats",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def update_publisher_stats(self) -> dict[str, Any]:
    """Recalculate aggregated stats for all publishers.

    Run daily at 3 AM UTC after rolling stats update.

    Returns:
        dict with status and publishers_updated count.
    """
    import asyncio
    from repotoire.marketplace.analytics import update_publisher_stats as do_update

    logger.info("Starting publisher stats update")

    async def run():
        async with get_async_session() as db:
            return await do_update(db)

    updated = asyncio.run(run())

    logger.info("Publisher stats update complete", publishers_updated=updated)

    return {
        "status": "completed",
        "publishers_updated": updated,
    }


@celery_app.task(
    name="repotoire.workers.analytics_tasks.cleanup_old_events",
    bind=True,
    max_retries=2,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def cleanup_old_events(self, days_to_keep: int = 90) -> dict[str, Any]:
    """Delete old events to manage table size.

    Run weekly on Sunday at 4 AM UTC.
    Daily snapshots are retained, raw events are pruned.

    Args:
        days_to_keep: Number of days of events to retain. Default 90.

    Returns:
        dict with status and events_deleted count.
    """
    import asyncio
    from repotoire.marketplace.analytics import cleanup_old_events as do_cleanup

    logger.info("Starting event cleanup", days_to_keep=days_to_keep)

    async def run():
        async with get_async_session() as db:
            return await do_cleanup(db, days_to_keep=days_to_keep)

    deleted = asyncio.run(run())

    logger.info("Event cleanup complete", events_deleted=deleted)

    return {
        "status": "completed",
        "events_deleted": deleted,
    }

"""Celery tasks for audit log maintenance.

This module contains periodic tasks for:
- Purging old audit logs based on retention policies
- Archiving audit logs to cold storage (future)

Usage:
    # Run the retention cleanup task manually
    from repotoire.workers.audit_tasks import cleanup_old_audit_logs
    cleanup_old_audit_logs.delay()

    # The task is also scheduled via Celery Beat (daily at 3 AM UTC)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from celery import shared_task
from sqlalchemy import delete, func, select

from repotoire.db.models import AuditLog, Organization
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Default retention period in days (2 years for SOC 2 compliance)
DEFAULT_RETENTION_DAYS = int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", "730"))

# Enterprise orgs may have custom retention periods
# This could be stored in Organization.metadata or a dedicated config table
MIN_RETENTION_DAYS = 90  # Minimum 90 days for any org
MAX_RETENTION_DAYS = 2555  # Maximum 7 years


@shared_task(
    name="repotoire.workers.audit_tasks.cleanup_old_audit_logs",
    bind=True,
    max_retries=3,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
)
def cleanup_old_audit_logs(
    self,
    retention_days: int | None = None,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete audit logs older than the retention period.

    This task runs daily to maintain audit log storage within compliance
    requirements while preventing unbounded table growth.

    Args:
        retention_days: Override default retention period. If None, uses
                       DEFAULT_RETENTION_DAYS (730 days / 2 years).
        batch_size: Number of records to delete per batch. Lower values
                   reduce lock contention but increase task duration.
        dry_run: If True, only counts records without deleting.

    Returns:
        dict with:
        - deleted_count: Number of records deleted (or would be deleted)
        - cutoff_date: The date threshold used
        - duration_seconds: How long the task took
        - dry_run: Whether this was a dry run
    """
    import time

    start_time = time.time()

    # Use provided retention or default
    if retention_days is None:
        retention_days = DEFAULT_RETENTION_DAYS

    # Validate retention period
    if retention_days < MIN_RETENTION_DAYS:
        logger.warning(
            f"Retention period {retention_days} days is below minimum "
            f"({MIN_RETENTION_DAYS}), using minimum."
        )
        retention_days = MIN_RETENTION_DAYS

    if retention_days > MAX_RETENTION_DAYS:
        logger.warning(
            f"Retention period {retention_days} days exceeds maximum "
            f"({MAX_RETENTION_DAYS}), using maximum."
        )
        retention_days = MAX_RETENTION_DAYS

    # Calculate cutoff date
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

    logger.info(
        f"{'[DRY RUN] ' if dry_run else ''}Cleaning audit logs older than "
        f"{cutoff_date.isoformat()} ({retention_days} days retention)"
    )

    total_deleted = 0

    with get_sync_session() as session:
        if dry_run:
            # Just count the records that would be deleted
            count_result = session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.timestamp < cutoff_date)
            )
            total_deleted = count_result.scalar() or 0

            logger.info(
                f"[DRY RUN] Would delete {total_deleted} audit logs "
                f"older than {cutoff_date.isoformat()}"
            )
        else:
            # Delete in batches to avoid long-running transactions
            # and reduce lock contention
            while True:
                # Find IDs of records to delete in this batch
                subquery = (
                    select(AuditLog.id)
                    .where(AuditLog.timestamp < cutoff_date)
                    .limit(batch_size)
                )

                # Delete the batch
                delete_stmt = (
                    delete(AuditLog)
                    .where(AuditLog.id.in_(subquery))
                )

                result = session.execute(delete_stmt)
                batch_deleted = result.rowcount
                session.commit()

                total_deleted += batch_deleted

                if batch_deleted < batch_size:
                    # No more records to delete
                    break

                logger.debug(
                    f"Deleted batch of {batch_deleted} audit logs, "
                    f"total so far: {total_deleted}"
                )

            logger.info(
                f"Deleted {total_deleted} audit logs older than "
                f"{cutoff_date.isoformat()}"
            )

    duration = time.time() - start_time

    return {
        "deleted_count": total_deleted,
        "cutoff_date": cutoff_date.isoformat(),
        "retention_days": retention_days,
        "duration_seconds": round(duration, 2),
        "dry_run": dry_run,
    }


@shared_task(
    name="repotoire.workers.audit_tasks.get_audit_log_stats",
    bind=True,
)
def get_audit_log_stats(self) -> dict[str, Any]:
    """Get statistics about audit log storage.

    Returns information useful for monitoring and capacity planning:
    - Total record count
    - Records by age bucket (last 24h, 7d, 30d, 90d, 1y, older)
    - Approximate storage size
    - Records eligible for cleanup

    Returns:
        dict with audit log statistics
    """
    with get_sync_session() as session:
        now = datetime.now(timezone.utc)

        # Total count
        total_result = session.execute(
            select(func.count()).select_from(AuditLog)
        )
        total_count = total_result.scalar() or 0

        # Count by age buckets
        age_buckets = {
            "last_24h": now - timedelta(hours=24),
            "last_7d": now - timedelta(days=7),
            "last_30d": now - timedelta(days=30),
            "last_90d": now - timedelta(days=90),
            "last_1y": now - timedelta(days=365),
        }

        bucket_counts = {}
        for bucket_name, cutoff in age_buckets.items():
            result = session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.timestamp >= cutoff)
            )
            bucket_counts[bucket_name] = result.scalar() or 0

        # Records eligible for cleanup (older than retention period)
        retention_cutoff = now - timedelta(days=DEFAULT_RETENTION_DAYS)
        cleanup_result = session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.timestamp < retention_cutoff)
        )
        eligible_for_cleanup = cleanup_result.scalar() or 0

        # Count by event source
        source_counts = {}
        for source in ["clerk", "application"]:
            result = session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.event_source == source)
            )
            source_counts[source] = result.scalar() or 0

        # Oldest and newest records
        oldest_result = session.execute(
            select(func.min(AuditLog.timestamp))
        )
        oldest = oldest_result.scalar()

        newest_result = session.execute(
            select(func.max(AuditLog.timestamp))
        )
        newest = newest_result.scalar()

    return {
        "total_count": total_count,
        "by_age": bucket_counts,
        "by_source": source_counts,
        "eligible_for_cleanup": eligible_for_cleanup,
        "retention_days": DEFAULT_RETENTION_DAYS,
        "oldest_record": oldest.isoformat() if oldest else None,
        "newest_record": newest.isoformat() if newest else None,
        "as_of": now.isoformat(),
    }

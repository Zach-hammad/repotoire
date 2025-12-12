"""Celery tasks for automated health checks and status updates.

This module provides background tasks for:
- Periodic health checks of service components
- Uptime percentage calculations
- Status notification delivery
- Historical data cleanup
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy import and_, delete, func, select

from repotoire.db.models.status import (
    ComponentStatus,
    StatusComponent,
    StatusSubscriber,
)
from repotoire.db.models.uptime import UptimeRecord
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger
from repotoire.workers.celery_app import celery_app

logger = get_logger(__name__)

# Configuration
HEALTH_CHECK_TIMEOUT = float(os.environ.get("HEALTH_CHECK_TIMEOUT", "5.0"))
DEGRADED_THRESHOLD_MS = int(os.environ.get("DEGRADED_THRESHOLD_MS", "1000"))
OUTAGE_THRESHOLD_MS = int(os.environ.get("OUTAGE_THRESHOLD_MS", "5000"))
WORKER_ID = os.environ.get("WORKER_ID", socket.gethostname())


def _check_component(url: str) -> tuple[ComponentStatus, int]:
    """Perform health check on a single component.

    Args:
        url: Health check URL to probe

    Returns:
        Tuple of (status, response_time_ms)
    """
    try:
        with httpx.Client(timeout=HEALTH_CHECK_TIMEOUT) as client:
            start = datetime.now(timezone.utc)
            response = client.get(url)
            elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

            # 5xx errors indicate major outage
            if response.status_code >= 500:
                return ComponentStatus.MAJOR_OUTAGE, elapsed_ms

            # 4xx errors indicate partial outage
            if response.status_code >= 400:
                return ComponentStatus.PARTIAL_OUTAGE, elapsed_ms

            # High latency indicates issues
            if elapsed_ms > OUTAGE_THRESHOLD_MS:
                return ComponentStatus.PARTIAL_OUTAGE, elapsed_ms

            if elapsed_ms > DEGRADED_THRESHOLD_MS:
                return ComponentStatus.DEGRADED, elapsed_ms

            return ComponentStatus.OPERATIONAL, elapsed_ms

    except httpx.TimeoutException:
        return ComponentStatus.MAJOR_OUTAGE, int(HEALTH_CHECK_TIMEOUT * 1000)
    except httpx.ConnectError:
        return ComponentStatus.MAJOR_OUTAGE, 0
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return ComponentStatus.MAJOR_OUTAGE, 0


@celery_app.task(name="repotoire.workers.health_checks.check_all_components")
def check_all_components() -> dict:
    """Check health of all components with health_check_url configured.

    This task runs every 30 seconds via Celery beat.

    Returns:
        Dict with counts of checked components and errors
    """
    checked = 0
    errors = 0
    status_changes = []

    with get_sync_session() as session:
        # Get all components with health check URLs
        result = session.execute(
            select(StatusComponent).where(StatusComponent.health_check_url.isnot(None))
        )
        components = result.scalars().all()

        for component in components:
            try:
                new_status, response_time = _check_component(component.health_check_url)

                # Track status changes
                if component.status != new_status:
                    status_changes.append({
                        "component": component.name,
                        "old_status": component.status.value,
                        "new_status": new_status.value,
                    })

                # Update component
                component.status = new_status
                component.response_time_ms = response_time
                component.last_checked_at = datetime.now(timezone.utc)

                # Record uptime data point
                record = UptimeRecord(
                    component_id=component.id,
                    timestamp=datetime.now(timezone.utc),
                    status=new_status,
                    response_time_ms=response_time,
                    checked_by=WORKER_ID,
                )
                session.add(record)

                checked += 1

            except Exception as e:
                logger.error(f"Health check failed for {component.name}: {e}")
                errors += 1

        session.commit()

    # Log status changes
    for change in status_changes:
        logger.warning(
            f"Component status changed: {change['component']} "
            f"{change['old_status']} -> {change['new_status']}"
        )

        # TODO: Trigger notifications for status changes
        # send_status_notifications.delay(
        #     component_name=change['component'],
        #     event="status_changed",
        #     old_status=change['old_status'],
        #     new_status=change['new_status'],
        # )

    return {
        "checked": checked,
        "errors": errors,
        "status_changes": len(status_changes),
    }


@celery_app.task(name="repotoire.workers.health_checks.calculate_uptime_percentages")
def calculate_uptime_percentages() -> dict:
    """Calculate 30-day rolling uptime for each component.

    This task runs every 5 minutes via Celery beat.

    Returns:
        Dict with component uptimes
    """
    updated = 0
    now = datetime.now(timezone.utc)
    thirty_days_ago = now - timedelta(days=30)

    with get_sync_session() as session:
        # Get all components
        result = session.execute(select(StatusComponent))
        components = result.scalars().all()

        for component in components:
            # Count total records and operational records in last 30 days
            stats = session.execute(
                select(
                    func.count().label("total"),
                    func.sum(
                        func.case(
                            (UptimeRecord.status == ComponentStatus.OPERATIONAL, 1),
                            else_=0,
                        )
                    ).label("operational"),
                ).where(
                    and_(
                        UptimeRecord.component_id == component.id,
                        UptimeRecord.timestamp >= thirty_days_ago,
                    )
                )
            ).first()

            if stats and stats.total and stats.total > 0:
                uptime_pct = (stats.operational / stats.total) * 100
                component.uptime_percentage = Decimal(str(round(uptime_pct, 2)))
                updated += 1

        session.commit()

    return {"updated": updated}


@celery_app.task(name="repotoire.workers.health_checks.send_status_notifications")
def send_status_notifications(
    incident_id: str | None = None,
    maintenance_id: str | None = None,
    component_name: str | None = None,
    event: str = "status_changed",
    **kwargs,
) -> dict:
    """Send notifications to all verified subscribers.

    Args:
        incident_id: ID of incident for incident notifications
        maintenance_id: ID of maintenance for maintenance notifications
        component_name: Component name for status change notifications
        event: Event type (incident_created, incident_updated, incident_resolved,
               maintenance_scheduled, status_changed)
        **kwargs: Additional event data

    Returns:
        Dict with notification counts
    """
    sent = 0
    failed = 0

    with get_sync_session() as session:
        # Get all verified subscribers
        result = session.execute(
            select(StatusSubscriber).where(StatusSubscriber.is_verified == True)  # noqa: E712
        )
        subscribers = result.scalars().all()

        for subscriber in subscribers:
            try:
                # TODO: Implement actual email sending
                # For now, just log the notification
                logger.info(
                    f"Would send {event} notification to {subscriber.email}",
                    extra={
                        "incident_id": incident_id,
                        "maintenance_id": maintenance_id,
                        "component_name": component_name,
                        "event": event,
                    },
                )
                sent += 1
            except Exception as e:
                logger.error(f"Failed to notify {subscriber.email}: {e}")
                failed += 1

    return {"sent": sent, "failed": failed}


@celery_app.task(name="repotoire.workers.health_checks.cleanup_old_uptime_data")
def cleanup_old_uptime_data(days: int = 90) -> dict:
    """Remove uptime records older than specified days.

    This task runs daily via Celery beat.

    Args:
        days: Number of days of history to retain (default: 90)

    Returns:
        Dict with count of deleted records
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with get_sync_session() as session:
        result = session.execute(
            delete(UptimeRecord).where(UptimeRecord.timestamp < cutoff)
        )
        deleted = result.rowcount
        session.commit()

    logger.info(f"Cleaned up {deleted} uptime records older than {days} days")

    return {"deleted": deleted}

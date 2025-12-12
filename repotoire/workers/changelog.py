"""Celery tasks for changelog publishing and notifications.

This module provides background tasks for:
- Auto-publishing scheduled changelog entries
- Sending instant notifications to subscribers
- Sending weekly and monthly digest emails
"""

from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import select

from repotoire.db.models.changelog import (
    ChangelogEntry,
    ChangelogSubscriber,
    DigestFrequency,
)
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Scheduled Publishing
# =============================================================================


@shared_task(name="repotoire.workers.changelog.publish_scheduled_entries")
def publish_scheduled_entries() -> dict:
    """Publish changelog entries that are scheduled for the current time.

    This task runs every 5 minutes via Celery beat and finds entries where:
    - is_draft = true
    - scheduled_for <= now

    For each matching entry:
    1. Set is_draft = false
    2. Set published_at = now
    3. Trigger notifications to instant subscribers

    Returns:
        Dictionary with count of published entries
    """
    now = datetime.now(timezone.utc)
    published_count = 0

    with get_sync_session() as session:
        # Find scheduled entries ready to publish
        result = session.execute(
            select(ChangelogEntry).where(
                ChangelogEntry.is_draft == True,  # noqa: E712
                ChangelogEntry.scheduled_for.isnot(None),
                ChangelogEntry.scheduled_for <= now,
            )
        )
        entries = result.scalars().all()

        for entry in entries:
            logger.info(
                f"Auto-publishing scheduled changelog entry: {entry.title}",
                extra={"entry_id": str(entry.id), "scheduled_for": str(entry.scheduled_for)},
            )

            # Publish the entry
            entry.is_draft = False
            entry.published_at = now

            # Queue instant notifications
            send_changelog_notifications.delay(entry_id=str(entry.id))

            published_count += 1

        session.commit()

    logger.info(f"Published {published_count} scheduled changelog entries")
    return {"published_count": published_count}


# =============================================================================
# Notification Tasks
# =============================================================================


@shared_task(name="repotoire.workers.changelog.send_changelog_notifications")
def send_changelog_notifications(entry_id: str) -> dict:
    """Send notifications to subscribers for a newly published entry.

    This task is triggered when an entry is published (either manually
    or via scheduled publishing). It sends instant emails to subscribers
    with digest_frequency='instant'.

    Args:
        entry_id: UUID string of the published entry

    Returns:
        Dictionary with notification statistics
    """
    from uuid import UUID

    entry_uuid = UUID(entry_id)
    sent_count = 0

    with get_sync_session() as session:
        # Get the entry
        entry = session.get(ChangelogEntry, entry_uuid)
        if not entry or entry.is_draft:
            logger.warning(f"Entry {entry_id} not found or still draft")
            return {"sent_count": 0, "error": "Entry not found or draft"}

        # Get instant subscribers
        result = session.execute(
            select(ChangelogSubscriber).where(
                ChangelogSubscriber.is_verified == True,  # noqa: E712
                ChangelogSubscriber.digest_frequency == DigestFrequency.INSTANT,
            )
        )
        subscribers = result.scalars().all()

        for subscriber in subscribers:
            # TODO: Integrate with email service (e.g., Resend, SendGrid)
            # For now, just log the notification
            logger.info(
                f"Would send changelog notification to {subscriber.email}",
                extra={
                    "entry_id": entry_id,
                    "subscriber_id": str(subscriber.id),
                    "entry_title": entry.title,
                },
            )
            # Example email sending:
            # send_email(
            #     to=subscriber.email,
            #     subject=f"New in Repotoire: {entry.title}",
            #     template="changelog_notification",
            #     context={
            #         "entry": entry,
            #         "unsubscribe_token": subscriber.unsubscribe_token,
            #     }
            # )
            sent_count += 1

    logger.info(
        f"Sent {sent_count} instant changelog notifications",
        extra={"entry_id": entry_id},
    )
    return {"sent_count": sent_count}


@shared_task(name="repotoire.workers.changelog.send_weekly_digest")
def send_weekly_digest() -> dict:
    """Send weekly changelog digest to subscribers.

    This task runs on Mondays at 9 AM UTC via Celery beat.
    Collects entries published in the past 7 days and sends
    a digest email to subscribers with digest_frequency='weekly'.

    Returns:
        Dictionary with digest statistics
    """
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    sent_count = 0
    entry_count = 0

    with get_sync_session() as session:
        # Get entries from the past week
        result = session.execute(
            select(ChangelogEntry)
            .where(
                ChangelogEntry.is_draft == False,  # noqa: E712
                ChangelogEntry.published_at.isnot(None),
                ChangelogEntry.published_at >= week_ago,
            )
            .order_by(ChangelogEntry.published_at.desc())
        )
        entries = result.scalars().all()
        entry_count = len(entries)

        if not entries:
            logger.info("No changelog entries in the past week, skipping digest")
            return {"sent_count": 0, "entry_count": 0}

        # Get weekly subscribers
        result = session.execute(
            select(ChangelogSubscriber).where(
                ChangelogSubscriber.is_verified == True,  # noqa: E712
                ChangelogSubscriber.digest_frequency == DigestFrequency.WEEKLY,
            )
        )
        subscribers = result.scalars().all()

        for subscriber in subscribers:
            # TODO: Integrate with email service
            logger.info(
                f"Would send weekly digest to {subscriber.email}",
                extra={
                    "subscriber_id": str(subscriber.id),
                    "entry_count": entry_count,
                },
            )
            # Example email sending:
            # send_email(
            #     to=subscriber.email,
            #     subject=f"Repotoire Weekly Update: {entry_count} new updates",
            #     template="changelog_weekly_digest",
            #     context={
            #         "entries": entries,
            #         "unsubscribe_token": subscriber.unsubscribe_token,
            #     }
            # )
            sent_count += 1

    logger.info(
        f"Sent {sent_count} weekly changelog digests",
        extra={"entry_count": entry_count},
    )
    return {"sent_count": sent_count, "entry_count": entry_count}


@shared_task(name="repotoire.workers.changelog.send_monthly_digest")
def send_monthly_digest() -> dict:
    """Send monthly changelog digest to subscribers.

    This task runs on the 1st of each month at 9 AM UTC via Celery beat.
    Collects entries published in the past 30 days and sends
    a digest email to subscribers with digest_frequency='monthly'.

    Returns:
        Dictionary with digest statistics
    """
    now = datetime.now(timezone.utc)
    month_ago = now - timedelta(days=30)
    sent_count = 0
    entry_count = 0

    with get_sync_session() as session:
        # Get entries from the past month
        result = session.execute(
            select(ChangelogEntry)
            .where(
                ChangelogEntry.is_draft == False,  # noqa: E712
                ChangelogEntry.published_at.isnot(None),
                ChangelogEntry.published_at >= month_ago,
            )
            .order_by(ChangelogEntry.published_at.desc())
        )
        entries = result.scalars().all()
        entry_count = len(entries)

        if not entries:
            logger.info("No changelog entries in the past month, skipping digest")
            return {"sent_count": 0, "entry_count": 0}

        # Get monthly subscribers
        result = session.execute(
            select(ChangelogSubscriber).where(
                ChangelogSubscriber.is_verified == True,  # noqa: E712
                ChangelogSubscriber.digest_frequency == DigestFrequency.MONTHLY,
            )
        )
        subscribers = result.scalars().all()

        for subscriber in subscribers:
            # TODO: Integrate with email service
            logger.info(
                f"Would send monthly digest to {subscriber.email}",
                extra={
                    "subscriber_id": str(subscriber.id),
                    "entry_count": entry_count,
                },
            )
            # Example email sending:
            # send_email(
            #     to=subscriber.email,
            #     subject=f"Repotoire Monthly Update: {entry_count} new updates",
            #     template="changelog_monthly_digest",
            #     context={
            #         "entries": entries,
            #         "unsubscribe_token": subscriber.unsubscribe_token,
            #     }
            # )
            sent_count += 1

    logger.info(
        f"Sent {sent_count} monthly changelog digests",
        extra={"entry_count": entry_count},
    )
    return {"sent_count": sent_count, "entry_count": entry_count}

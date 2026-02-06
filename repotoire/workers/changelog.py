"""Celery tasks for changelog publishing and notifications.

This module provides background tasks for:
- Auto-publishing scheduled changelog entries
- Sending instant notifications to subscribers
- Sending weekly and monthly digest emails
"""

import asyncio
import os
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
from repotoire.services.email import get_email_service

logger = get_logger(__name__)

# Check if email is configured
_EMAIL_CONFIGURED = bool(os.environ.get("RESEND_API_KEY"))


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
            try:
                if _EMAIL_CONFIGURED:
                    # Build entry dict for template
                    entry_dict = {
                        "title": entry.title,
                        "slug": entry.slug,
                        "summary": entry.summary,
                        "features": entry.features or [],
                        "improvements": entry.improvements or [],
                        "fixes": entry.fixes or [],
                        "published_at": entry.published_at,
                    }
                    email_service = get_email_service()
                    asyncio.get_event_loop().run_until_complete(
                        email_service.send_changelog_notification(
                            to_email=subscriber.email,
                            entry=entry_dict,
                            unsubscribe_token=subscriber.unsubscribe_token,
                        )
                    )
                    logger.info(
                        f"Sent changelog notification to {subscriber.email}",
                        extra={"entry_id": entry_id, "subscriber_id": str(subscriber.id)},
                    )
                else:
                    logger.debug(
                        f"Email not configured, skipping notification to {subscriber.email}",
                        extra={"entry_id": entry_id},
                    )
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send changelog notification to {subscriber.email}: {e}")

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
            try:
                if _EMAIL_CONFIGURED:
                    email_service = get_email_service()
                    asyncio.get_event_loop().run_until_complete(
                        email_service.send_changelog_weekly_digest(
                            to_email=subscriber.email,
                            entries=entries,
                            unsubscribe_token=subscriber.unsubscribe_token,
                        )
                    )
                    logger.info(
                        f"Sent weekly digest to {subscriber.email}",
                        extra={"subscriber_id": str(subscriber.id), "entry_count": entry_count},
                    )
                else:
                    logger.debug(f"Email not configured, skipping weekly digest to {subscriber.email}")
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send weekly digest to {subscriber.email}: {e}")

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
            try:
                if _EMAIL_CONFIGURED:
                    email_service = get_email_service()
                    asyncio.get_event_loop().run_until_complete(
                        email_service.send_changelog_monthly_digest(
                            to_email=subscriber.email,
                            entries=entries,
                            unsubscribe_token=subscriber.unsubscribe_token,
                        )
                    )
                    logger.info(
                        f"Sent monthly digest to {subscriber.email}",
                        extra={"subscriber_id": str(subscriber.id), "entry_count": entry_count},
                    )
                else:
                    logger.debug(f"Email not configured, skipping monthly digest to {subscriber.email}")
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send monthly digest to {subscriber.email}: {e}")

    logger.info(
        f"Sent {sent_count} monthly changelog digests",
        extra={"entry_count": entry_count},
    )
    return {"sent_count": sent_count, "entry_count": entry_count}

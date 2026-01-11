"""Notification service for in-app and email notifications.

This module provides notification functionality for:
- Analysis complete/failed notifications
- Health regression alerts
- New finding alerts
- Fix suggestions
- Team notifications
- Billing events
"""

from __future__ import annotations

import os
from typing import Any, Optional
from uuid import UUID

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repotoire.db.models import (
    EmailPreferences,
    InAppNotification,
    NotificationType,
    User,
)
from repotoire.db.session import get_db
from repotoire.logging_config import get_logger
from repotoire.services.email import EmailService

logger = get_logger(__name__)


# Notification type to title templates
NOTIFICATION_TITLES = {
    NotificationType.ANALYSIS_COMPLETE: "Analysis Complete",
    NotificationType.ANALYSIS_FAILED: "Analysis Failed",
    NotificationType.NEW_FINDING: "New Findings Detected",
    NotificationType.FIX_SUGGESTION: "AI Fix Available",
    NotificationType.HEALTH_REGRESSION: "Health Score Regression",
    NotificationType.TEAM_INVITE: "Team Invitation",
    NotificationType.TEAM_ROLE_CHANGE: "Role Updated",
    NotificationType.BILLING_EVENT: "Billing Update",
    NotificationType.SYSTEM: "System Notification",
}

# Notification type to email template mapping
EMAIL_TEMPLATES = {
    NotificationType.ANALYSIS_COMPLETE: "notifications/analysis_complete",
    NotificationType.ANALYSIS_FAILED: "notifications/analysis_failed",
    NotificationType.NEW_FINDING: "notifications/new_finding",
    NotificationType.FIX_SUGGESTION: "notifications/fix_suggestion",
    NotificationType.HEALTH_REGRESSION: "notifications/health_regression",
    NotificationType.TEAM_INVITE: "notifications/team_invite",
    NotificationType.TEAM_ROLE_CHANGE: "notifications/team_role_change",
    NotificationType.BILLING_EVENT: "notifications/billing_event",
}

# Notification type to email subject mapping
EMAIL_SUBJECTS = {
    NotificationType.ANALYSIS_COMPLETE: "Analysis Complete",
    NotificationType.ANALYSIS_FAILED: "Analysis Failed",
    NotificationType.NEW_FINDING: "New Critical Findings",
    NotificationType.FIX_SUGGESTION: "AI Fix Ready for Review",
    NotificationType.HEALTH_REGRESSION: "Health Score Alert",
    NotificationType.TEAM_INVITE: "Team Invitation",
    NotificationType.TEAM_ROLE_CHANGE: "Your Role Has Changed",
    NotificationType.BILLING_EVENT: "Billing Update",
}


class NotificationService:
    """Service for sending in-app and email notifications.

    Supports multiple notification channels:
    - In-app notifications (stored in database)
    - Email notifications (via EmailService)
    - Slack webhooks (for admin alerts)

    Usage:
        notifications = NotificationService(session)
        await notifications.send(
            user_id=user.id,
            notification_type=NotificationType.ANALYSIS_COMPLETE,
            message="Analysis completed with 87 health score",
            action_url="/dashboard/repos/1",
            metadata={"repo_name": "my-repo", "health_score": 87},
        )
    """

    def __init__(
        self,
        session: AsyncSession,
        email_service: Optional[EmailService] = None,
        slack_webhook_url: Optional[str] = None,
    ):
        """Initialize the notification service.

        Args:
            session: Database session for creating notifications
            email_service: Email service instance (creates one if not provided)
            slack_webhook_url: Optional Slack webhook URL for admin alerts
        """
        self.session = session
        self.email_service = email_service or EmailService()
        self.slack_webhook_url = slack_webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
        self._user_email_cache: dict[str, str] = {}

    async def send(
        self,
        user_id: UUID,
        notification_type: NotificationType,
        message: str,
        title: Optional[str] = None,
        action_url: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[InAppNotification]:
        """Send a notification to a user.

        Creates in-app notification and optionally sends email based on
        user preferences.

        Args:
            user_id: Internal user UUID to notify
            notification_type: Type of notification
            message: Notification message body
            title: Optional custom title (uses default if not provided)
            action_url: Optional URL for the notification action
            metadata: Additional context-specific data

        Returns:
            Created InAppNotification if successful, None otherwise
        """
        # Get user preferences
        prefs = await self._get_user_preferences(user_id)

        # Check if this notification type is enabled
        if not self._should_send_notification(notification_type, prefs):
            logger.debug(
                f"Notification disabled by user preferences",
                extra={
                    "user_id": str(user_id),
                    "notification_type": notification_type.value,
                },
            )
            return None

        notification = None

        # Create in-app notification if enabled
        if prefs.get("in_app_notifications", True):
            try:
                notification = await self._create_inapp_notification(
                    user_id=user_id,
                    notification_type=notification_type,
                    title=title or NOTIFICATION_TITLES.get(
                        notification_type, "Notification"
                    ),
                    message=message,
                    action_url=action_url,
                    metadata=metadata,
                )
            except Exception as e:
                logger.error(
                    f"Failed to create in-app notification",
                    extra={
                        "user_id": str(user_id),
                        "notification_type": notification_type.value,
                        "error": str(e),
                    },
                )

        # Send email notification if the type has a template
        if notification_type in EMAIL_TEMPLATES:
            try:
                await self._send_email(
                    user_id=user_id,
                    notification_type=notification_type,
                    message=message,
                    action_url=action_url,
                    metadata=metadata,
                )
            except Exception as e:
                logger.error(
                    f"Failed to send email notification",
                    extra={
                        "user_id": str(user_id),
                        "notification_type": notification_type.value,
                        "error": str(e),
                    },
                )

        return notification

    async def send_to_clerk_user(
        self,
        clerk_user_id: str,
        notification_type: NotificationType,
        message: str,
        title: Optional[str] = None,
        action_url: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[InAppNotification]:
        """Send a notification to a user by Clerk ID.

        Convenience method that looks up the internal user ID.

        Args:
            clerk_user_id: Clerk user ID
            notification_type: Type of notification
            message: Notification message body
            title: Optional custom title
            action_url: Optional URL for the notification action
            metadata: Additional context-specific data

        Returns:
            Created InAppNotification if successful, None otherwise
        """
        # Look up user by Clerk ID
        result = await self.session.execute(
            select(User.id).where(User.clerk_user_id == clerk_user_id)
        )
        user_id = result.scalar_one_or_none()

        if not user_id:
            logger.warning(
                f"User not found for Clerk ID",
                extra={"clerk_user_id": clerk_user_id},
            )
            return None

        return await self.send(
            user_id=user_id,
            notification_type=notification_type,
            message=message,
            title=title,
            action_url=action_url,
            metadata=metadata,
        )

    async def _get_user_preferences(self, user_id: UUID) -> dict[str, Any]:
        """Get user notification preferences.

        Returns default preferences if none are set.
        """
        result = await self.session.execute(
            select(EmailPreferences).where(EmailPreferences.user_id == user_id)
        )
        prefs = result.scalar_one_or_none()

        if not prefs:
            # Return defaults
            return {
                "analysis_complete": True,
                "analysis_failed": True,
                "health_regression": True,
                "weekly_digest": False,
                "team_notifications": True,
                "billing_notifications": True,
                "in_app_notifications": True,
                "regression_threshold": 10,
            }

        return {
            "analysis_complete": prefs.analysis_complete,
            "analysis_failed": prefs.analysis_failed,
            "health_regression": prefs.health_regression,
            "weekly_digest": prefs.weekly_digest,
            "team_notifications": prefs.team_notifications,
            "billing_notifications": prefs.billing_notifications,
            "in_app_notifications": getattr(prefs, "in_app_notifications", True),
            "regression_threshold": prefs.regression_threshold,
        }

    def _should_send_notification(
        self,
        notification_type: NotificationType,
        prefs: dict[str, Any],
    ) -> bool:
        """Check if this notification type should be sent based on preferences."""
        type_to_pref = {
            NotificationType.ANALYSIS_COMPLETE: "analysis_complete",
            NotificationType.ANALYSIS_FAILED: "analysis_failed",
            NotificationType.HEALTH_REGRESSION: "health_regression",
            NotificationType.TEAM_INVITE: "team_notifications",
            NotificationType.TEAM_ROLE_CHANGE: "team_notifications",
            NotificationType.BILLING_EVENT: "billing_notifications",
        }

        pref_key = type_to_pref.get(notification_type)
        if pref_key:
            return prefs.get(pref_key, True)

        # Default to enabled for types without specific preferences
        return True

    async def _create_inapp_notification(
        self,
        user_id: UUID,
        notification_type: NotificationType,
        title: str,
        message: str,
        action_url: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> InAppNotification:
        """Create an in-app notification in the database."""
        notification = InAppNotification(
            user_id=user_id,
            type=notification_type.value,
            title=title,
            message=message,
            action_url=action_url,
            extra_data=metadata,
        )
        self.session.add(notification)
        await self.session.commit()
        await self.session.refresh(notification)

        logger.info(
            f"Created in-app notification",
            extra={
                "notification_id": str(notification.id),
                "user_id": str(user_id),
                "type": notification_type.value,
            },
        )

        return notification

    async def _send_email(
        self,
        user_id: UUID,
        notification_type: NotificationType,
        message: str,
        action_url: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """Send email notification."""
        template = EMAIL_TEMPLATES.get(notification_type)
        subject = EMAIL_SUBJECTS.get(notification_type)

        if not template or not subject:
            return None

        # Get user email
        user_email = await self._get_user_email(user_id)
        if not user_email:
            logger.warning(
                f"Could not get email for user",
                extra={"user_id": str(user_id)},
            )
            return None

        # Build email context
        context = {
            "message": message,
            "action_url": action_url,
            **(metadata or {}),
        }

        try:
            email_id = await self.email_service.send(
                to=user_email,
                subject=subject,
                template_name=template,
                context=context,
            )
            logger.info(
                f"Sent email notification",
                extra={
                    "user_id": str(user_id),
                    "notification_type": notification_type.value,
                    "email_id": email_id,
                },
            )
            return email_id
        except Exception as e:
            logger.error(
                f"Failed to send email: {e}",
                extra={
                    "user_id": str(user_id),
                    "notification_type": notification_type.value,
                },
            )
            raise

    async def _get_user_email(self, user_id: UUID) -> Optional[str]:
        """Get user email from database."""
        result = await self.session.execute(
            select(User.email).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def send_admin_alert(
        self,
        alert_type: str,
        data: dict[str, Any],
    ) -> None:
        """Send an alert to admins via Slack.

        Used for critical events like system issues or abuse detection.

        Args:
            alert_type: Type of alert
            data: Alert data
        """
        if self.slack_webhook_url:
            try:
                await self._send_slack_alert(alert_type, data)
            except Exception as e:
                logger.error(f"Failed to send Slack alert: {e}")

        logger.warning(
            f"Admin alert: {alert_type}",
            extra={"alert_type": alert_type, "data": data},
        )

    async def _send_slack_alert(
        self,
        alert_type: str,
        data: dict[str, Any],
    ) -> None:
        """Send alert to Slack webhook."""
        import httpx

        if not self.slack_webhook_url:
            return

        message = f"*{alert_type.upper()}*\n"
        for key, value in data.items():
            message += f"* {key}: {value}\n"

        payload = {
            "text": message,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": message,
                    },
                },
            ],
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            await client.post(self.slack_webhook_url, json=payload)


# Helper functions for creating specific notification types


async def notify_analysis_complete(
    session: AsyncSession,
    user_id: UUID,
    repo_name: str,
    health_score: int,
    finding_count: int,
    repo_id: str,
) -> Optional[InAppNotification]:
    """Create notification for completed analysis."""
    service = NotificationService(session)
    return await service.send(
        user_id=user_id,
        notification_type=NotificationType.ANALYSIS_COMPLETE,
        message=f"{repo_name} finished analyzing with a health score of {health_score}",
        action_url=f"/dashboard/repos/{repo_id}",
        metadata={
            "repo_name": repo_name,
            "health_score": health_score,
            "finding_count": finding_count,
        },
    )


async def notify_analysis_failed(
    session: AsyncSession,
    user_id: UUID,
    repo_name: str,
    error_message: str,
    repo_id: str,
) -> Optional[InAppNotification]:
    """Create notification for failed analysis."""
    service = NotificationService(session)
    return await service.send(
        user_id=user_id,
        notification_type=NotificationType.ANALYSIS_FAILED,
        message=f"Analysis failed for {repo_name}: {error_message}",
        action_url=f"/dashboard/repos/{repo_id}",
        metadata={
            "repo_name": repo_name,
            "error_message": error_message,
        },
    )


async def notify_new_findings(
    session: AsyncSession,
    user_id: UUID,
    repo_name: str,
    finding_count: int,
    severity: str,
    repo_id: str,
) -> Optional[InAppNotification]:
    """Create notification for new critical/high findings."""
    service = NotificationService(session)
    return await service.send(
        user_id=user_id,
        notification_type=NotificationType.NEW_FINDING,
        title=f"{finding_count} New {severity.title()} Finding{'s' if finding_count != 1 else ''}",
        message=f"Found potential {severity} issues in {repo_name}",
        action_url=f"/dashboard/findings?severity={severity}&repository_id={repo_id}",
        metadata={
            "repo_name": repo_name,
            "finding_count": finding_count,
            "severity": severity,
        },
    )


async def notify_fix_suggestion(
    session: AsyncSession,
    user_id: UUID,
    finding_title: str,
    file_path: str,
    fix_id: str,
) -> Optional[InAppNotification]:
    """Create notification for new AI fix suggestion."""
    service = NotificationService(session)
    return await service.send(
        user_id=user_id,
        notification_type=NotificationType.FIX_SUGGESTION,
        message=f"Automated fix ready for \"{finding_title}\" in {file_path}",
        action_url=f"/dashboard/fixes/{fix_id}",
        metadata={
            "finding_title": finding_title,
            "file_path": file_path,
        },
    )


async def notify_health_regression(
    session: AsyncSession,
    user_id: UUID,
    repo_name: str,
    old_score: int,
    new_score: int,
    repo_id: str,
) -> Optional[InAppNotification]:
    """Create notification for health score regression."""
    drop = old_score - new_score
    service = NotificationService(session)
    return await service.send(
        user_id=user_id,
        notification_type=NotificationType.HEALTH_REGRESSION,
        message=f"Health score for {repo_name} dropped by {drop} points (from {old_score} to {new_score})",
        action_url=f"/dashboard/repos/{repo_id}",
        metadata={
            "repo_name": repo_name,
            "old_score": old_score,
            "new_score": new_score,
            "drop": drop,
        },
    )


# FastAPI dependency
_notification_service: Optional[NotificationService] = None


def get_notification_service(db: AsyncSession = Depends(get_db)) -> NotificationService:
    """FastAPI dependency that creates a notification service.

    Args:
        db: Database session (injected by FastAPI)

    Returns:
        NotificationService instance
    """
    return NotificationService(db)

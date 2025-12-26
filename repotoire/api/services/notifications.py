"""Notification service for marketplace events.

This module provides notification functionality for:
- Asset approval/rejection/changes requested
- Community report alerts
- Publisher warnings
- Asset unpublish notifications
"""

from __future__ import annotations

import os
from typing import Any, Optional

from repotoire.logging_config import get_logger
from repotoire.services.email import EmailService

logger = get_logger(__name__)


class NotificationService:
    """Service for sending marketplace notifications.

    Supports multiple notification channels:
    - Email (via EmailService)
    - In-app notifications (future)
    - Slack webhooks (future)

    Usage:
        notifications = NotificationService()
        await notifications.send(
            user_id="user_xxx",
            notification_type="asset_approved",
            data={"asset_name": "My Asset", "version": "1.0.0"},
        )
    """

    # Notification type to email template mapping
    EMAIL_TEMPLATES = {
        "asset_approved": "marketplace/asset_approved",
        "asset_rejected": "marketplace/asset_rejected",
        "changes_requested": "marketplace/changes_requested",
        "asset_reported": "marketplace/asset_reported",
        "asset_unpublished": "marketplace/asset_unpublished",
        "warning_issued": "marketplace/warning_issued",
    }

    # Notification type to email subject mapping
    EMAIL_SUBJECTS = {
        "asset_approved": "Your asset has been approved!",
        "asset_rejected": "Your asset was not approved",
        "changes_requested": "Changes requested for your asset",
        "asset_reported": "Your asset has been reported",
        "asset_unpublished": "Your asset has been unpublished",
        "warning_issued": "Warning: Policy violation on your asset",
    }

    def __init__(
        self,
        email_service: Optional[EmailService] = None,
        enable_email: bool = True,
        enable_inapp: bool = True,
        slack_webhook_url: Optional[str] = None,
    ):
        """Initialize the notification service.

        Args:
            email_service: Email service instance (creates one if not provided)
            enable_email: Whether to send email notifications
            enable_inapp: Whether to create in-app notifications
            slack_webhook_url: Optional Slack webhook URL for admin alerts
        """
        self.email_service = email_service or EmailService()
        self.enable_email = enable_email
        self.enable_inapp = enable_inapp
        self.slack_webhook_url = slack_webhook_url or os.environ.get("SLACK_WEBHOOK_URL")
        self._user_email_cache: dict[str, str] = {}

    async def send(
        self,
        user_id: str,
        notification_type: str,
        data: dict[str, Any],
    ) -> bool:
        """Send a notification to a user.

        Args:
            user_id: Clerk user ID to notify
            notification_type: Type of notification (e.g., "asset_approved")
            data: Notification data (varies by type)

        Returns:
            True if notification was sent successfully
        """
        success = True

        # Send email if enabled and user has email
        if self.enable_email:
            try:
                await self._send_email(user_id, notification_type, data)
            except Exception as e:
                logger.error(
                    f"Failed to send email notification",
                    extra={
                        "user_id": user_id,
                        "notification_type": notification_type,
                        "error": str(e),
                    },
                )
                success = False

        # Create in-app notification if enabled
        if self.enable_inapp:
            try:
                await self._create_inapp_notification(user_id, notification_type, data)
            except Exception as e:
                logger.error(
                    f"Failed to create in-app notification",
                    extra={
                        "user_id": user_id,
                        "notification_type": notification_type,
                        "error": str(e),
                    },
                )
                success = False

        return success

    async def _send_email(
        self,
        user_id: str,
        notification_type: str,
        data: dict[str, Any],
    ) -> Optional[str]:
        """Send email notification.

        Args:
            user_id: Clerk user ID
            notification_type: Type of notification
            data: Notification data

        Returns:
            Email ID if sent, None otherwise
        """
        # Get template and subject
        template = self.EMAIL_TEMPLATES.get(notification_type)
        subject = self.EMAIL_SUBJECTS.get(notification_type)

        if not template or not subject:
            logger.warning(
                f"No email template for notification type: {notification_type}"
            )
            return None

        # Get user email
        user_email = await self._get_user_email(user_id)
        if not user_email:
            logger.warning(
                f"Could not get email for user: {user_id}",
                extra={"notification_type": notification_type},
            )
            return None

        # Customize subject with asset name if available
        if "asset_name" in data:
            subject = f"{subject}: {data['asset_name']}"

        try:
            email_id = await self.email_service.send(
                to=user_email,
                subject=subject,
                template_name=template,
                context=data,
            )
            logger.info(
                f"Sent email notification",
                extra={
                    "user_id": user_id,
                    "notification_type": notification_type,
                    "email_id": email_id,
                },
            )
            return email_id
        except Exception as e:
            logger.error(
                f"Failed to send email: {e}",
                extra={
                    "user_id": user_id,
                    "notification_type": notification_type,
                },
            )
            raise

    async def _create_inapp_notification(
        self,
        user_id: str,
        notification_type: str,
        data: dict[str, Any],
    ) -> None:
        """Create in-app notification.

        This is a placeholder for future in-app notification support.
        Currently just logs the notification.

        Args:
            user_id: Clerk user ID
            notification_type: Type of notification
            data: Notification data
        """
        # TODO: Implement in-app notifications table and storage
        logger.info(
            f"In-app notification (not yet implemented)",
            extra={
                "user_id": user_id,
                "notification_type": notification_type,
                "data": data,
            },
        )

    async def _get_user_email(self, user_id: str) -> Optional[str]:
        """Get user email from Clerk.

        Args:
            user_id: Clerk user ID

        Returns:
            User email if found, None otherwise
        """
        # Check cache first
        if user_id in self._user_email_cache:
            return self._user_email_cache[user_id]

        try:
            from clerk_backend_api import Clerk

            clerk_secret = os.environ.get("CLERK_SECRET_KEY")
            if not clerk_secret:
                logger.warning("CLERK_SECRET_KEY not set, cannot fetch user email")
                return None

            clerk = Clerk(bearer_auth=clerk_secret)
            user = clerk.users.get(user_id=user_id)

            if user and user.email_addresses:
                # Get primary email
                primary_email = next(
                    (e for e in user.email_addresses if e.id == user.primary_email_address_id),
                    user.email_addresses[0] if user.email_addresses else None,
                )
                if primary_email:
                    email = primary_email.email_address
                    self._user_email_cache[user_id] = email
                    return email

            return None
        except Exception as e:
            logger.error(f"Failed to get user email from Clerk: {e}")
            return None

    async def send_admin_alert(
        self,
        alert_type: str,
        data: dict[str, Any],
    ) -> None:
        """Send an alert to admins.

        Used for critical events like multiple reports on an asset.

        Args:
            alert_type: Type of alert
            data: Alert data
        """
        # Send to Slack if configured
        if self.slack_webhook_url:
            try:
                await self._send_slack_alert(alert_type, data)
            except Exception as e:
                logger.error(f"Failed to send Slack alert: {e}")

        # Log the alert
        logger.warning(
            f"Admin alert: {alert_type}",
            extra={"alert_type": alert_type, "data": data},
        )

    async def _send_slack_alert(
        self,
        alert_type: str,
        data: dict[str, Any],
    ) -> None:
        """Send alert to Slack webhook.

        Args:
            alert_type: Type of alert
            data: Alert data
        """
        import httpx

        if not self.slack_webhook_url:
            return

        # Format message
        message = f"*{alert_type.upper()}*\n"
        for key, value in data.items():
            message += f"• {key}: {value}\n"

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

        async with httpx.AsyncClient() as client:
            await client.post(self.slack_webhook_url, json=payload)


# FastAPI dependency
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """Get or create the notification service singleton.

    Returns:
        NotificationService instance
    """
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service

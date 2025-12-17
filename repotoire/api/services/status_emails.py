"""Email notification service for status page updates.

This module provides email templates and sending logic for:
- Subscription verification emails
- Incident notifications (created, updated, resolved)
- Scheduled maintenance notifications
- Status change notifications
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# Configuration
BASE_URL = os.environ.get("STATUS_PAGE_URL", "https://status.repotoire.io")
API_URL = os.environ.get("API_URL", "https://api.repotoire.io")
FROM_EMAIL = os.environ.get("STATUS_EMAIL_FROM", "status@repotoire.io")
FROM_NAME = os.environ.get("STATUS_EMAIL_FROM_NAME", "Repotoire Status")

# Email sending configuration
# Max concurrent emails (SendGrid: 100/s, SES: 14/s default, adjust per provider)
EMAIL_MAX_CONCURRENCY = int(os.environ.get("EMAIL_MAX_CONCURRENCY", "10"))
# Max retries for transient failures
EMAIL_MAX_RETRIES = int(os.environ.get("EMAIL_MAX_RETRIES", "3"))
# Base delay for exponential backoff (seconds)
EMAIL_RETRY_BASE_DELAY = float(os.environ.get("EMAIL_RETRY_BASE_DELAY", "1.0"))
# Batch size for processing large subscriber lists (0 = no batching)
EMAIL_BATCH_SIZE = int(os.environ.get("EMAIL_BATCH_SIZE", "100"))


@dataclass
class EmailMessage:
    """Email message structure."""

    to: str
    subject: str
    html: str
    text: str


# =============================================================================
# Email Templates
# =============================================================================


def _base_template(content: str, title: str) -> str:
    """Wrap content in base HTML template."""
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 600px;
            margin: 0 auto;
            padding: 20px;
        }}
        .header {{
            text-align: center;
            padding: 20px 0;
            border-bottom: 1px solid #e5e5e5;
        }}
        .content {{
            padding: 20px 0;
        }}
        .footer {{
            padding: 20px 0;
            border-top: 1px solid #e5e5e5;
            font-size: 12px;
            color: #666;
            text-align: center;
        }}
        .button {{
            display: inline-block;
            padding: 12px 24px;
            background-color: #0066cc;
            color: white !important;
            text-decoration: none;
            border-radius: 4px;
            margin: 10px 0;
        }}
        .severity-critical {{
            color: #dc3545;
            font-weight: bold;
        }}
        .severity-major {{
            color: #fd7e14;
            font-weight: bold;
        }}
        .severity-minor {{
            color: #ffc107;
            font-weight: bold;
        }}
        .status-operational {{
            color: #28a745;
        }}
        .status-degraded {{
            color: #ffc107;
        }}
        .status-partial_outage {{
            color: #fd7e14;
        }}
        .status-major_outage {{
            color: #dc3545;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Repotoire Status</h1>
    </div>
    <div class="content">
        {content}
    </div>
    <div class="footer">
        <p>You're receiving this because you subscribed to status updates.</p>
        <p><a href="{{{{unsubscribe_url}}}}">Unsubscribe</a> | <a href="{BASE_URL}">View Status Page</a></p>
    </div>
</body>
</html>"""


def create_verification_email(email: str, verification_token: str) -> EmailMessage:
    """Create status page email verification message."""
    verify_url = f"{API_URL}/api/v1/status/verify?token={verification_token}"

    html_content = f"""
    <h2>Verify your email address</h2>
    <p>Thank you for subscribing to Repotoire status updates!</p>
    <p>Please click the button below to verify your email address:</p>
    <p><a href="{verify_url}" class="button">Verify Email</a></p>
    <p>Or copy and paste this link into your browser:</p>
    <p style="word-break: break-all;">{verify_url}</p>
    <p>If you didn't request this, you can safely ignore this email.</p>
    """

    text_content = f"""
Verify your email address

Thank you for subscribing to Repotoire status updates!

Please click the link below to verify your email address:
{verify_url}

If you didn't request this, you can safely ignore this email.
    """

    return EmailMessage(
        to=email,
        subject="Verify your Repotoire Status subscription",
        html=_base_template(html_content, "Verify Email"),
        text=text_content,
    )


def create_changelog_verification_email(email: str, verification_token: str) -> EmailMessage:
    """Create changelog subscription verification email."""
    verify_url = f"{API_URL}/api/v1/changelog/subscribe/verify?token={verification_token}"

    html_content = f"""
    <h2>Verify your email address</h2>
    <p>Thank you for subscribing to Repotoire changelog updates!</p>
    <p>Stay informed about new features, improvements, and bug fixes.</p>
    <p>Please click the button below to verify your email address:</p>
    <p><a href="{verify_url}" class="button">Verify Email</a></p>
    <p>Or copy and paste this link into your browser:</p>
    <p style="word-break: break-all;">{verify_url}</p>
    <p>If you didn't request this, you can safely ignore this email.</p>
    """

    text_content = f"""
Verify your email address

Thank you for subscribing to Repotoire changelog updates!

Stay informed about new features, improvements, and bug fixes.

Please click the link below to verify your email address:
{verify_url}

If you didn't request this, you can safely ignore this email.
    """

    return EmailMessage(
        to=email,
        subject="Verify your Repotoire Changelog subscription",
        html=_base_template(html_content, "Verify Email"),
        text=text_content,
    )


def create_incident_created_email(
    email: str,
    unsubscribe_token: str,
    incident_title: str,
    incident_severity: str,
    incident_message: str,
    affected_components: list[str],
    started_at: datetime,
) -> EmailMessage:
    """Create incident notification email."""
    unsubscribe_url = f"{API_URL}/api/v1/status/unsubscribe?token={unsubscribe_token}"
    severity_class = f"severity-{incident_severity.lower()}"

    components_html = ", ".join(affected_components) if affected_components else "Multiple services"

    html_content = f"""
    <h2>New Incident: {incident_title}</h2>
    <p><span class="{severity_class}">[{incident_severity.upper()}]</span> - Started at {started_at.strftime('%Y-%m-%d %H:%M UTC')}</p>
    <p><strong>Affected:</strong> {components_html}</p>
    <p>{incident_message}</p>
    <p><a href="{BASE_URL}" class="button">View Status Page</a></p>
    """

    text_content = f"""
New Incident: {incident_title}

Severity: {incident_severity.upper()}
Started: {started_at.strftime('%Y-%m-%d %H:%M UTC')}
Affected: {components_html}

{incident_message}

View status page: {BASE_URL}
    """

    return EmailMessage(
        to=email,
        subject=f"[{incident_severity.upper()}] {incident_title} - Repotoire Status",
        html=_base_template(html_content, "Incident Notification").replace("{{unsubscribe_url}}", unsubscribe_url),
        text=text_content,
    )


def create_incident_updated_email(
    email: str,
    unsubscribe_token: str,
    incident_title: str,
    incident_severity: str,
    update_message: str,
    new_status: str,
    updated_at: datetime,
) -> EmailMessage:
    """Create incident update notification email."""
    unsubscribe_url = f"{API_URL}/api/v1/status/unsubscribe?token={unsubscribe_token}"

    html_content = f"""
    <h2>Update: {incident_title}</h2>
    <p><strong>Status:</strong> {new_status.replace('_', ' ').title()}</p>
    <p><strong>Updated:</strong> {updated_at.strftime('%Y-%m-%d %H:%M UTC')}</p>
    <p>{update_message}</p>
    <p><a href="{BASE_URL}" class="button">View Status Page</a></p>
    """

    text_content = f"""
Update: {incident_title}

Status: {new_status.replace('_', ' ').title()}
Updated: {updated_at.strftime('%Y-%m-%d %H:%M UTC')}

{update_message}

View status page: {BASE_URL}
    """

    return EmailMessage(
        to=email,
        subject=f"Update: {incident_title} - Repotoire Status",
        html=_base_template(html_content, "Incident Update").replace("{{unsubscribe_url}}", unsubscribe_url),
        text=text_content,
    )


def create_incident_resolved_email(
    email: str,
    unsubscribe_token: str,
    incident_title: str,
    incident_severity: str,
    resolution_message: str,
    resolved_at: datetime,
    postmortem_url: str | None = None,
) -> EmailMessage:
    """Create incident resolved notification email."""
    unsubscribe_url = f"{API_URL}/api/v1/status/unsubscribe?token={unsubscribe_token}"

    postmortem_html = ""
    postmortem_text = ""
    if postmortem_url:
        postmortem_html = f'<p><a href="{postmortem_url}">View Postmortem</a></p>'
        postmortem_text = f"\nPostmortem: {postmortem_url}"

    html_content = f"""
    <h2>Resolved: {incident_title}</h2>
    <p class="status-operational"><strong>All systems operational</strong></p>
    <p><strong>Resolved:</strong> {resolved_at.strftime('%Y-%m-%d %H:%M UTC')}</p>
    <p>{resolution_message}</p>
    {postmortem_html}
    <p><a href="{BASE_URL}" class="button">View Status Page</a></p>
    """

    text_content = f"""
Resolved: {incident_title}

All systems operational.

Resolved: {resolved_at.strftime('%Y-%m-%d %H:%M UTC')}

{resolution_message}
{postmortem_text}

View status page: {BASE_URL}
    """

    return EmailMessage(
        to=email,
        subject=f"Resolved: {incident_title} - Repotoire Status",
        html=_base_template(html_content, "Incident Resolved").replace("{{unsubscribe_url}}", unsubscribe_url),
        text=text_content,
    )


def create_maintenance_scheduled_email(
    email: str,
    unsubscribe_token: str,
    maintenance_title: str,
    maintenance_description: str | None,
    affected_components: list[str],
    scheduled_start: datetime,
    scheduled_end: datetime,
) -> EmailMessage:
    """Create scheduled maintenance notification email."""
    unsubscribe_url = f"{API_URL}/api/v1/status/unsubscribe?token={unsubscribe_token}"
    components_html = ", ".join(affected_components) if affected_components else "Multiple services"

    description_html = f"<p>{maintenance_description}</p>" if maintenance_description else ""

    html_content = f"""
    <h2>Scheduled Maintenance: {maintenance_title}</h2>
    <p><strong>When:</strong> {scheduled_start.strftime('%Y-%m-%d %H:%M')} - {scheduled_end.strftime('%H:%M UTC')}</p>
    <p><strong>Affected:</strong> {components_html}</p>
    {description_html}
    <p><a href="{BASE_URL}" class="button">View Status Page</a></p>
    """

    text_content = f"""
Scheduled Maintenance: {maintenance_title}

When: {scheduled_start.strftime('%Y-%m-%d %H:%M')} - {scheduled_end.strftime('%H:%M UTC')}
Affected: {components_html}

{maintenance_description or ''}

View status page: {BASE_URL}
    """

    return EmailMessage(
        to=email,
        subject=f"Scheduled Maintenance: {maintenance_title} - Repotoire Status",
        html=_base_template(html_content, "Scheduled Maintenance").replace("{{unsubscribe_url}}", unsubscribe_url),
        text=text_content,
    )


# =============================================================================
# Email Sending
# =============================================================================


async def send_email(message: EmailMessage) -> bool:
    """Send an email message.

    This is a placeholder implementation. In production, integrate with
    an email service like SendGrid, AWS SES, or Postmark.

    Args:
        message: EmailMessage to send

    Returns:
        True if sent successfully, False otherwise
    """
    # Check if email sending is configured
    sendgrid_api_key = os.environ.get("SENDGRID_API_KEY")
    aws_ses_region = os.environ.get("AWS_SES_REGION")

    if sendgrid_api_key:
        return await _send_via_sendgrid(message, sendgrid_api_key)
    elif aws_ses_region:
        return await _send_via_ses(message, aws_ses_region)
    else:
        # Log email in development
        logger.info(
            f"Email would be sent (no provider configured)",
            extra={
                "to": message.to,
                "subject": message.subject,
            },
        )
        return True


async def _send_via_sendgrid(message: EmailMessage, api_key: str) -> bool:
    """Send email via SendGrid.

    Args:
        message: EmailMessage to send
        api_key: SendGrid API key

    Returns:
        True if sent successfully
    """
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{"to": [{"email": message.to}]}],
                    "from": {"email": FROM_EMAIL, "name": FROM_NAME},
                    "subject": message.subject,
                    "content": [
                        {"type": "text/plain", "value": message.text},
                        {"type": "text/html", "value": message.html},
                    ],
                },
            )

            if response.status_code in (200, 202):
                logger.info(f"Email sent via SendGrid to {message.to}")
                return True
            else:
                logger.error(f"SendGrid error: {response.status_code} {response.text}")
                return False

    except Exception as e:
        logger.error(f"Failed to send email via SendGrid: {e}")
        return False


async def _send_via_ses(message: EmailMessage, region: str) -> bool:
    """Send email via AWS SES (async).

    Args:
        message: EmailMessage to send
        region: AWS region

    Returns:
        True if sent successfully
    """
    try:
        import aioboto3
    except ImportError:
        logger.error(
            "aioboto3 not installed. Install with: pip install repotoire[saas]"
        )
        return False

    try:
        session = aioboto3.Session()
        async with session.client("ses", region_name=region) as ses:
            response = await ses.send_email(
                Source=f"{FROM_NAME} <{FROM_EMAIL}>",
                Destination={"ToAddresses": [message.to]},
                Message={
                    "Subject": {"Data": message.subject},
                    "Body": {
                        "Text": {"Data": message.text},
                        "Html": {"Data": message.html},
                    },
                },
            )

        logger.info(
            f"Email sent via SES to {message.to}",
            extra={"message_id": response["MessageId"]}
        )
        return True

    except Exception as e:
        logger.error(f"Failed to send email via SES: {e}")
        return False


# =============================================================================
# Batch Sending
# =============================================================================


async def notify_subscribers_incident_created(
    subscribers: list[dict[str, Any]],
    incident_title: str,
    incident_severity: str,
    incident_message: str,
    affected_components: list[str],
    started_at: datetime,
) -> dict[str, int]:
    """Send incident created notifications to all subscribers.

    Sends emails concurrently with bounded parallelism, retry logic,
    and batched processing for large subscriber lists.

    Configuration via environment variables:
        EMAIL_MAX_CONCURRENCY: Max concurrent emails (default: 10)
        EMAIL_MAX_RETRIES: Max retries per email (default: 3)
        EMAIL_RETRY_BASE_DELAY: Base delay for backoff in seconds (default: 1.0)
        EMAIL_BATCH_SIZE: Batch size for large lists (default: 100, 0=disabled)

    Args:
        subscribers: List of subscriber dicts with email and unsubscribe_token
        incident_title: Title of the incident
        incident_severity: Severity level
        incident_message: Initial incident message
        affected_components: List of affected component names
        started_at: When incident started

    Returns:
        Dict with sent and failed counts
    """
    if not subscribers:
        return {"sent": 0, "failed": 0}

    semaphore = asyncio.Semaphore(EMAIL_MAX_CONCURRENCY)

    async def send_one_with_retry(subscriber: dict[str, Any]) -> bool:
        """Send email to one subscriber with bounded concurrency and retries."""
        email = subscriber.get("email", "unknown")

        for attempt in range(EMAIL_MAX_RETRIES):
            try:
                async with semaphore:
                    message = create_incident_created_email(
                        email=subscriber["email"],
                        unsubscribe_token=subscriber["unsubscribe_token"],
                        incident_title=incident_title,
                        incident_severity=incident_severity,
                        incident_message=incident_message,
                        affected_components=affected_components,
                        started_at=started_at,
                    )
                    result = await send_email(message)
                    if result:
                        return True
                    # send_email returned False (non-exception failure)
                    logger.warning(
                        f"Email send returned False for {email} "
                        f"(attempt {attempt + 1}/{EMAIL_MAX_RETRIES})"
                    )
            except Exception as e:
                logger.warning(
                    f"Email send failed for {email} "
                    f"(attempt {attempt + 1}/{EMAIL_MAX_RETRIES}): {e}"
                )

            # Exponential backoff before retry (except on last attempt)
            if attempt < EMAIL_MAX_RETRIES - 1:
                delay = EMAIL_RETRY_BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)

        # All retries exhausted
        logger.error(f"Email send failed permanently for {email} after {EMAIL_MAX_RETRIES} attempts")
        return False

    async def process_batch(batch: list[dict[str, Any]]) -> list[bool]:
        """Process a batch of subscribers concurrently."""
        results = await asyncio.gather(
            *[send_one_with_retry(sub) for sub in batch],
            return_exceptions=True,
        )
        # Convert exceptions to False
        return [r if r is True else False for r in results]

    # Process in batches to avoid memory issues with very large lists
    all_results: list[bool] = []

    if EMAIL_BATCH_SIZE > 0 and len(subscribers) > EMAIL_BATCH_SIZE:
        logger.info(
            f"Processing {len(subscribers)} subscribers in batches of {EMAIL_BATCH_SIZE}"
        )
        for i in range(0, len(subscribers), EMAIL_BATCH_SIZE):
            batch = subscribers[i : i + EMAIL_BATCH_SIZE]
            batch_results = await process_batch(batch)
            all_results.extend(batch_results)
            logger.debug(
                f"Batch {i // EMAIL_BATCH_SIZE + 1} complete: "
                f"{sum(batch_results)}/{len(batch_results)} sent"
            )
    else:
        all_results = await process_batch(subscribers)

    sent = sum(1 for r in all_results if r is True)
    failed = len(all_results) - sent

    logger.info(
        f"Email notification complete: {sent} sent, {failed} failed "
        f"out of {len(subscribers)} subscribers"
    )

    return {"sent": sent, "failed": failed}

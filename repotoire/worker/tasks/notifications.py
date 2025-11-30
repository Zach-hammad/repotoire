"""
Notification tasks for Repotoire.

This module contains tasks for:
- Email notifications (analysis complete, weekly digest)
- GitHub PR comments
- Customer webhook delivery
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Any
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
import structlog

from repotoire.worker.celery_app import celery_app
from repotoire.worker.utils.task_helpers import (
    get_user_by_id,
    get_repository_by_id,
    get_github_token_for_repo,
    get_customer_webhook,
    record_webhook_delivery,
)

logger = structlog.get_logger(__name__)

# Email service configuration
EMAIL_SERVICE_URL = os.getenv("EMAIL_SERVICE_URL", "https://api.resend.com/emails")
EMAIL_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "noreply@repotoire.dev")

# GitHub API
GITHUB_API_BASE = "https://api.github.com"


@celery_app.task(
    name="repotoire.worker.tasks.notifications.send_analysis_complete_email",
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
    soft_time_limit=30,
    time_limit=60,
)
def send_analysis_complete_email(
    user_id: str,
    repo_id: str,
    health_score: float,
) -> dict[str, Any]:
    """
    Send email notification when analysis is complete.

    Args:
        user_id: User ID to send email to
        repo_id: Repository that was analyzed
        health_score: Overall health score

    Returns:
        Dictionary with email delivery status
    """
    log = logger.bind(
        user_id=user_id,
        repo_id=repo_id,
        health_score=health_score,
    )

    # Get user details
    user = get_user_by_id(user_id)
    if not user:
        log.warning("user_not_found")
        return {"status": "skipped", "reason": "user_not_found"}

    # Check user notification preferences
    if not user.email_notifications_enabled:
        log.debug("email_notifications_disabled")
        return {"status": "skipped", "reason": "notifications_disabled"}

    # Get repository details
    repo = get_repository_by_id(repo_id)
    if not repo:
        log.warning("repository_not_found")
        return {"status": "skipped", "reason": "repo_not_found"}

    # Determine grade from score
    grade = _score_to_grade(health_score)

    # Build email content
    subject = f"[Repotoire] Analysis Complete: {repo.full_name} ({grade})"
    html_body = _build_analysis_email_html(
        repo_name=repo.full_name,
        health_score=health_score,
        grade=grade,
        dashboard_url=f"https://app.repotoire.dev/repos/{repo_id}",
    )

    # Send email
    try:
        response = _send_email(
            to_email=user.email,
            subject=subject,
            html_body=html_body,
        )

        log.info("analysis_email_sent", email_id=response.get("id"))
        return {
            "status": "sent",
            "email_id": response.get("id"),
            "to": user.email,
        }

    except Exception as exc:
        log.exception("email_send_failed", error=str(exc))
        raise


@celery_app.task(
    name="repotoire.worker.tasks.notifications.post_pr_comment",
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
    soft_time_limit=30,
    time_limit=60,
)
def post_pr_comment(
    repo_id: str,
    pr_number: int,
    comment_body: str,
) -> dict[str, Any]:
    """
    Post a comment on a GitHub pull request.

    Args:
        repo_id: Repository ID
        pr_number: Pull request number
        comment_body: Markdown comment body

    Returns:
        Dictionary with comment creation status
    """
    log = logger.bind(
        repo_id=repo_id,
        pr_number=pr_number,
    )

    # Get repository
    repo = get_repository_by_id(repo_id)
    if not repo:
        log.warning("repository_not_found")
        return {"status": "skipped", "reason": "repo_not_found"}

    # Get GitHub installation token
    token = get_github_token_for_repo(repo)
    if not token:
        log.warning("github_token_unavailable")
        return {"status": "skipped", "reason": "no_github_token"}

    # Check if we already commented (update existing comment)
    existing_comment_id = _find_existing_comment(
        repo_full_name=repo.full_name,
        pr_number=pr_number,
        token=token,
    )

    try:
        if existing_comment_id:
            # Update existing comment
            comment = _update_pr_comment(
                repo_full_name=repo.full_name,
                comment_id=existing_comment_id,
                body=comment_body,
                token=token,
            )
            log.info("pr_comment_updated", comment_id=comment["id"])
            return {
                "status": "updated",
                "comment_id": comment["id"],
                "comment_url": comment["html_url"],
            }
        else:
            # Create new comment
            comment = _create_pr_comment(
                repo_full_name=repo.full_name,
                pr_number=pr_number,
                body=comment_body,
                token=token,
            )
            log.info("pr_comment_created", comment_id=comment["id"])
            return {
                "status": "created",
                "comment_id": comment["id"],
                "comment_url": comment["html_url"],
            }

    except Exception as exc:
        log.exception("pr_comment_failed", error=str(exc))
        raise


@celery_app.task(
    name="repotoire.worker.tasks.notifications.send_webhook_to_customer",
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,
    retry_backoff_max=3600,  # Max 1 hour backoff
    max_retries=5,
    soft_time_limit=30,
    time_limit=60,
)
def send_webhook_to_customer(
    webhook_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Deliver a webhook to a customer-configured endpoint.

    Implements secure webhook delivery with:
    - HMAC signature for payload verification
    - Retry with exponential backoff
    - Delivery status tracking

    Args:
        webhook_id: Webhook configuration ID
        event_type: Event type (e.g., "analysis.completed")
        payload: Event payload to deliver

    Returns:
        Dictionary with delivery status
    """
    log = logger.bind(
        webhook_id=webhook_id,
        event_type=event_type,
    )

    # Get webhook configuration
    webhook = get_customer_webhook(webhook_id)
    if not webhook:
        log.warning("webhook_not_found")
        return {"status": "skipped", "reason": "webhook_not_found"}

    if not webhook.enabled:
        log.debug("webhook_disabled")
        return {"status": "skipped", "reason": "webhook_disabled"}

    # Check if event type is subscribed
    if event_type not in webhook.subscribed_events:
        log.debug("event_not_subscribed")
        return {"status": "skipped", "reason": "event_not_subscribed"}

    # Validate URL (security: prevent SSRF)
    if not _is_valid_webhook_url(webhook.url):
        log.warning("invalid_webhook_url", url=webhook.url)
        return {"status": "failed", "reason": "invalid_url"}

    # Build webhook payload
    timestamp = int(time.time())
    delivery_id = f"whd_{webhook_id}_{timestamp}"

    webhook_payload = {
        "id": delivery_id,
        "event": event_type,
        "timestamp": timestamp,
        "data": payload,
    }

    # Generate HMAC signature
    signature = _generate_webhook_signature(
        payload=webhook_payload,
        secret=webhook.secret,
    )

    # Send webhook
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                webhook.url,
                json=webhook_payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Repotoire-Signature": signature,
                    "X-Repotoire-Event": event_type,
                    "X-Repotoire-Delivery": delivery_id,
                },
            )

            # Record delivery attempt
            record_webhook_delivery(
                webhook_id=webhook_id,
                delivery_id=delivery_id,
                event_type=event_type,
                status_code=response.status_code,
                success=response.is_success,
            )

            if response.is_success:
                log.info(
                    "webhook_delivered",
                    delivery_id=delivery_id,
                    status_code=response.status_code,
                )
                return {
                    "status": "delivered",
                    "delivery_id": delivery_id,
                    "status_code": response.status_code,
                }
            else:
                log.warning(
                    "webhook_delivery_failed",
                    delivery_id=delivery_id,
                    status_code=response.status_code,
                )
                # Retry on 5xx errors
                if response.status_code >= 500:
                    raise httpx.HTTPError(
                        f"Webhook delivery failed: {response.status_code}"
                    )
                return {
                    "status": "failed",
                    "delivery_id": delivery_id,
                    "status_code": response.status_code,
                }

    except httpx.TimeoutException:
        log.warning("webhook_timeout", delivery_id=delivery_id)
        record_webhook_delivery(
            webhook_id=webhook_id,
            delivery_id=delivery_id,
            event_type=event_type,
            status_code=0,
            success=False,
            error="timeout",
        )
        raise

    except Exception as exc:
        log.exception("webhook_error", delivery_id=delivery_id, error=str(exc))
        raise


@celery_app.task(
    name="repotoire.worker.tasks.notifications.send_weekly_digest",
    soft_time_limit=300,
    time_limit=360,
)
def send_weekly_digest() -> dict[str, Any]:
    """
    Send weekly digest emails to all users with activity.

    This is a periodic task that runs every Monday at 9 AM UTC.

    Returns:
        Dictionary with digest send statistics
    """
    log = logger.bind(task="weekly_digest")
    log.info("starting_weekly_digest")

    from repotoire.worker.utils.task_helpers import get_users_with_activity

    # Get users with repository activity in the past week
    one_week_ago = datetime.utcnow() - timedelta(days=7)
    users = get_users_with_activity(since=one_week_ago)

    sent_count = 0
    failed_count = 0

    for user in users:
        try:
            # Get user's repository summary
            repos_summary = _get_user_repos_summary(user.id, since=one_week_ago)

            if not repos_summary:
                continue  # No activity to report

            # Build digest email
            subject = "[Repotoire] Your Weekly Code Health Digest"
            html_body = _build_digest_email_html(
                user_name=user.name or user.email.split("@")[0],
                repos_summary=repos_summary,
                dashboard_url="https://app.repotoire.dev/dashboard",
            )

            # Send email
            _send_email(
                to_email=user.email,
                subject=subject,
                html_body=html_body,
            )
            sent_count += 1

        except Exception as exc:
            log.warning(
                "digest_send_failed",
                user_id=str(user.id),
                error=str(exc),
            )
            failed_count += 1

    log.info(
        "weekly_digest_complete",
        sent_count=sent_count,
        failed_count=failed_count,
    )

    return {
        "status": "completed",
        "sent_count": sent_count,
        "failed_count": failed_count,
    }


# =============================================================================
# Helper Functions
# =============================================================================


def _score_to_grade(score: float) -> str:
    """Convert health score to letter grade."""
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"


def _send_email(to_email: str, subject: str, html_body: str) -> dict[str, Any]:
    """Send email via Resend API."""
    if not EMAIL_API_KEY:
        logger.warning("email_api_key_not_configured")
        return {"id": "mock-email-id", "status": "skipped"}

    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            EMAIL_SERVICE_URL,
            headers={
                "Authorization": f"Bearer {EMAIL_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": EMAIL_FROM_ADDRESS,
                "to": to_email,
                "subject": subject,
                "html": html_body,
            },
        )
        response.raise_for_status()
        return response.json()


def _build_analysis_email_html(
    repo_name: str,
    health_score: float,
    grade: str,
    dashboard_url: str,
) -> str:
    """Build HTML email for analysis completion."""
    grade_color = {
        "A": "#22c55e",
        "B": "#84cc16",
        "C": "#eab308",
        "D": "#f97316",
        "F": "#ef4444",
    }.get(grade, "#6b7280")

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .score-badge {{ display: inline-block; padding: 12px 24px; background: {grade_color}; color: white; font-size: 24px; font-weight: bold; border-radius: 8px; }}
            .button {{ display: inline-block; padding: 12px 24px; background: #3b82f6; color: white; text-decoration: none; border-radius: 6px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Analysis Complete</h1>
            <p>Your repository <strong>{repo_name}</strong> has been analyzed.</p>

            <div style="text-align: center; margin: 30px 0;">
                <div class="score-badge">{grade}</div>
                <p style="margin-top: 10px; color: #6b7280;">Health Score: {health_score:.1f}/100</p>
            </div>

            <p>View detailed findings and recommendations in your dashboard:</p>
            <a href="{dashboard_url}" class="button">View Dashboard</a>

            <hr style="margin: 40px 0; border: none; border-top: 1px solid #e5e7eb;">
            <p style="color: #9ca3af; font-size: 12px;">
                You're receiving this because you have email notifications enabled.
                <a href="https://app.repotoire.dev/settings/notifications">Manage preferences</a>
            </p>
        </div>
    </body>
    </html>
    """


def _build_digest_email_html(
    user_name: str,
    repos_summary: list[dict[str, Any]],
    dashboard_url: str,
) -> str:
    """Build HTML email for weekly digest."""
    repos_html = ""
    for repo in repos_summary:
        grade_color = {
            "A": "#22c55e",
            "B": "#84cc16",
            "C": "#eab308",
            "D": "#f97316",
            "F": "#ef4444",
        }.get(repo["grade"], "#6b7280")

        trend = repo.get("trend", 0)
        trend_icon = "↑" if trend > 0 else "↓" if trend < 0 else "→"
        trend_color = "#22c55e" if trend > 0 else "#ef4444" if trend < 0 else "#6b7280"

        repos_html += f"""
        <tr>
            <td style="padding: 12px 0;">{repo["name"]}</td>
            <td style="padding: 12px 0; text-align: center;">
                <span style="color: {grade_color}; font-weight: bold;">{repo["grade"]}</span>
            </td>
            <td style="padding: 12px 0; text-align: right;">
                <span style="color: {trend_color};">{trend_icon} {abs(trend):.1f}</span>
            </td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th {{ text-align: left; padding: 12px 0; border-bottom: 2px solid #e5e7eb; }}
            .button {{ display: inline-block; padding: 12px 24px; background: #3b82f6; color: white; text-decoration: none; border-radius: 6px; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Weekly Code Health Digest</h1>
            <p>Hi {user_name}, here's your code health summary for the past week:</p>

            <table>
                <thead>
                    <tr>
                        <th>Repository</th>
                        <th style="text-align: center;">Grade</th>
                        <th style="text-align: right;">Change</th>
                    </tr>
                </thead>
                <tbody>
                    {repos_html}
                </tbody>
            </table>

            <a href="{dashboard_url}" class="button">View Full Dashboard</a>

            <hr style="margin: 40px 0; border: none; border-top: 1px solid #e5e7eb;">
            <p style="color: #9ca3af; font-size: 12px;">
                You're receiving this weekly digest because you have it enabled.
                <a href="https://app.repotoire.dev/settings/notifications">Manage preferences</a>
            </p>
        </div>
    </body>
    </html>
    """


def _find_existing_comment(
    repo_full_name: str,
    pr_number: int,
    token: str,
) -> int | None:
    """Find existing Repotoire comment on PR."""
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments"

    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )

        if not response.is_success:
            return None

        for comment in response.json():
            # Look for our signature in the comment
            if "Repotoire Code Health Analysis" in comment.get("body", ""):
                return comment["id"]

    return None


def _create_pr_comment(
    repo_full_name: str,
    pr_number: int,
    body: str,
    token: str,
) -> dict[str, Any]:
    """Create a new PR comment."""
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/{pr_number}/comments"

    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={"body": body},
        )
        response.raise_for_status()
        return response.json()


def _update_pr_comment(
    repo_full_name: str,
    comment_id: int,
    body: str,
    token: str,
) -> dict[str, Any]:
    """Update an existing PR comment."""
    url = f"{GITHUB_API_BASE}/repos/{repo_full_name}/issues/comments/{comment_id}"

    with httpx.Client(timeout=10.0) as client:
        response = client.patch(
            url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={"body": body},
        )
        response.raise_for_status()
        return response.json()


def _generate_webhook_signature(payload: dict[str, Any], secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


def _is_valid_webhook_url(url: str) -> bool:
    """Validate webhook URL (prevent SSRF)."""
    try:
        parsed = urlparse(url)

        # Must be HTTPS
        if parsed.scheme != "https":
            return False

        # Must have a host
        if not parsed.netloc:
            return False

        # Block private/local addresses
        hostname = parsed.hostname or ""
        blocked_patterns = [
            "localhost",
            "127.",
            "10.",
            "172.16.",
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
            "192.168.",
            "169.254.",
            "0.0.0.0",
            "::1",
            "fc00:",
            "fe80:",
        ]

        for pattern in blocked_patterns:
            if hostname.startswith(pattern) or hostname == pattern.rstrip("."):
                return False

        return True

    except Exception:
        return False


def _get_user_repos_summary(
    user_id: str,
    since: datetime,
) -> list[dict[str, Any]]:
    """Get summary of user's repositories with recent activity."""
    from repotoire.worker.utils.task_helpers import get_user_repositories_with_scores

    repos = get_user_repositories_with_scores(user_id, since=since)

    return [
        {
            "name": repo.full_name,
            "grade": _score_to_grade(repo.latest_score or 0),
            "score": repo.latest_score or 0,
            "trend": repo.score_trend or 0,  # Difference from previous analysis
        }
        for repo in repos
    ]

"""Post-analysis hooks for notifications and integrations.

This module contains Celery tasks that are triggered after analysis completion:
- on_analysis_complete: Send notifications on successful analysis
- on_analysis_failed: Send alerts on analysis failures
- post_pr_comment: Post analysis results as a PR comment
- send_webhook_to_customer: Deliver webhooks to customer-configured endpoints
- send_weekly_digest: Send weekly code health digest emails
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select

from repotoire.db.models import (
    AnalysisRun,
    AnalysisStatus,
    MemberRole,
    Organization,
    OrganizationMembership,
    Repository,
)
from repotoire.db.session import get_sync_session
from repotoire.logging_config import get_logger
from repotoire.workers.celery_app import celery_app

if TYPE_CHECKING:
    from repotoire.db.models import User

logger = get_logger(__name__)

APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://app.repotoire.io")


@celery_app.task(name="repotoire.workers.hooks.on_analysis_complete")
def on_analysis_complete(analysis_run_id: str) -> dict:
    """Post-analysis hooks for successful completion.

    - Checks for health regression and sends alert if threshold exceeded
    - Sends analysis complete notification if user has enabled it

    Args:
        analysis_run_id: UUID of the completed AnalysisRun.

    Returns:
        dict with status and notification info.
    """
    try:
        with get_sync_session() as session:
            analysis = session.get(AnalysisRun, UUID(analysis_run_id))
            if not analysis:
                logger.warning(f"AnalysisRun {analysis_run_id} not found")
                return {"status": "skipped", "reason": "analysis_not_found"}

            repo = analysis.repository
            org = repo.organization

            # Get organization owner for notifications
            owner = _get_org_owner(session, org.id)
            if not owner:
                logger.warning(f"No owner found for organization {org.id}")
                return {"status": "skipped", "reason": "no_owner"}

            # Check for health regression
            previous = _get_previous_analysis(
                session, repo.id, exclude_id=analysis.id
            )

            if previous and previous.health_score and analysis.health_score:
                drop = previous.health_score - analysis.health_score

                # Get user's regression threshold preference
                threshold = 10  # Default threshold
                if owner.email_preferences:
                    threshold = owner.email_preferences.regression_threshold

                if drop >= threshold:
                    _send_regression_alert(
                        owner=owner,
                        repo=repo,
                        old_score=previous.health_score,
                        new_score=analysis.health_score,
                    )
                    return {
                        "status": "notified",
                        "notification_type": "regression_alert",
                        "score_drop": drop,
                    }

            # Send completion notification if enabled
            if owner.email_preferences is None or owner.email_preferences.analysis_complete:
                _send_completion_notification(
                    owner=owner,
                    repo=repo,
                    health_score=analysis.health_score,
                )
                return {
                    "status": "notified",
                    "notification_type": "analysis_complete",
                }

            return {"status": "skipped", "reason": "notifications_disabled"}

    except Exception as e:
        logger.exception(f"on_analysis_complete failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task(name="repotoire.workers.hooks.on_analysis_failed")
def on_analysis_failed(analysis_run_id: str, error_message: str) -> dict:
    """Post-analysis hooks for failures.

    Sends failure notification to organization owner.

    Args:
        analysis_run_id: UUID of the failed AnalysisRun.
        error_message: Error message describing the failure.

    Returns:
        dict with status and notification info.
    """
    try:
        with get_sync_session() as session:
            analysis = session.get(AnalysisRun, UUID(analysis_run_id))
            if not analysis:
                logger.warning(f"AnalysisRun {analysis_run_id} not found")
                return {"status": "skipped", "reason": "analysis_not_found"}

            repo = analysis.repository
            org = repo.organization

            owner = _get_org_owner(session, org.id)
            if not owner:
                return {"status": "skipped", "reason": "no_owner"}

            # Send failure notification if enabled
            if owner.email_preferences is None or owner.email_preferences.analysis_failed:
                _send_failure_notification(
                    owner=owner,
                    repo=repo,
                    error_message=error_message,
                )
                return {
                    "status": "notified",
                    "notification_type": "analysis_failed",
                }

            return {"status": "skipped", "reason": "notifications_disabled"}

    except Exception as e:
        logger.exception(f"on_analysis_failed failed: {e}")
        return {"status": "error", "error": str(e)}


@celery_app.task(name="repotoire.workers.hooks.post_pr_comment")
def post_pr_comment(
    repo_id: str,
    pr_number: int,
    analysis_run_id: str,
) -> dict:
    """Post analysis results as a PR comment.

    Creates a formatted comment on the pull request with:
    - Health score and score delta
    - Category breakdowns (structure, quality, architecture)
    - Findings count
    - Link to full report

    Args:
        repo_id: UUID of the Repository.
        pr_number: Pull request number.
        analysis_run_id: UUID of the AnalysisRun.

    Returns:
        dict with status and comment_id if posted.
    """
    try:
        with get_sync_session() as session:
            analysis = session.get(AnalysisRun, UUID(analysis_run_id))
            if not analysis:
                return {"status": "skipped", "reason": "analysis_not_found"}

            repo = session.get(Repository, UUID(repo_id))
            if not repo:
                return {"status": "skipped", "reason": "repo_not_found"}

            org = repo.organization

            # Build comment body
            comment = _format_pr_comment(analysis)

            # Post via GitHub API
            github_token = _get_github_token(org)
            if not github_token:
                logger.warning("No GitHub token available for PR comment")
                return {"status": "skipped", "reason": "no_github_token"}

            # Parse owner/repo from full_name
            parts = repo.full_name.split("/")
            if len(parts) != 2:
                return {"status": "skipped", "reason": "invalid_repo_name"}

            owner, repo_name = parts

            comment_id = _create_pr_comment(
                github_token=github_token,
                owner=owner,
                repo=repo_name,
                pr_number=pr_number,
                body=comment,
            )

            return {
                "status": "posted",
                "comment_id": comment_id,
                "pr_number": pr_number,
            }

    except Exception as e:
        logger.exception(f"post_pr_comment failed: {e}")
        return {"status": "error", "error": str(e)}


# =============================================================================
# Helper Functions
# =============================================================================


def _get_org_owner(session, org_id: UUID) -> "User | None":
    """Get the owner of an organization.

    Args:
        session: SQLAlchemy session.
        org_id: Organization UUID.

    Returns:
        User model instance or None.
    """
    from sqlalchemy import select
    from repotoire.db.models import User

    result = session.execute(
        select(User)
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .where(OrganizationMembership.organization_id == org_id)
        .where(OrganizationMembership.role == MemberRole.OWNER.value)
        .limit(1)
    )
    return result.scalar_one_or_none()


def _get_previous_analysis(
    session,
    repo_id: UUID,
    exclude_id: UUID,
) -> AnalysisRun | None:
    """Get the most recent completed analysis before the current one.

    Args:
        session: SQLAlchemy session.
        repo_id: Repository UUID.
        exclude_id: AnalysisRun UUID to exclude.

    Returns:
        AnalysisRun model instance or None.
    """
    from sqlalchemy import select
    from repotoire.db.models import AnalysisStatus

    result = session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.repository_id == repo_id)
        .where(AnalysisRun.id != exclude_id)
        .where(AnalysisRun.status == AnalysisStatus.COMPLETED)
        .order_by(AnalysisRun.completed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _send_regression_alert(
    owner: "User",
    repo: Repository,
    old_score: int,
    new_score: int,
) -> None:
    """Send health regression alert email.

    Args:
        owner: User to notify.
        repo: Repository with regression.
        old_score: Previous health score.
        new_score: New (lower) health score.
    """
    try:
        from repotoire.services.email import get_email_service
        import asyncio

        email_service = get_email_service()
        dashboard_url = f"{APP_BASE_URL}/repos/{repo.id}"

        # Run async email send in sync context
        asyncio.get_event_loop().run_until_complete(
            email_service.send_health_regression_alert(
                user_email=owner.email,
                repo_name=repo.full_name,
                old_score=old_score,
                new_score=new_score,
                dashboard_url=dashboard_url,
            )
        )
    except Exception as e:
        logger.exception(f"Failed to send regression alert: {e}")


def _send_completion_notification(
    owner: "User",
    repo: Repository,
    health_score: int | None,
) -> None:
    """Send analysis completion notification email.

    Args:
        owner: User to notify.
        repo: Repository analyzed.
        health_score: Analysis health score.
    """
    if health_score is None:
        return

    try:
        from repotoire.services.email import get_email_service
        import asyncio

        email_service = get_email_service()
        dashboard_url = f"{APP_BASE_URL}/repos/{repo.id}"

        asyncio.get_event_loop().run_until_complete(
            email_service.send_analysis_complete(
                user_email=owner.email,
                repo_name=repo.full_name,
                health_score=health_score,
                dashboard_url=dashboard_url,
            )
        )
    except Exception as e:
        logger.exception(f"Failed to send completion notification: {e}")


def _send_failure_notification(
    owner: "User",
    repo: Repository,
    error_message: str,
) -> None:
    """Send analysis failure notification email.

    Args:
        owner: User to notify.
        repo: Repository that failed analysis.
        error_message: Error description.
    """
    try:
        from repotoire.services.email import get_email_service
        import asyncio

        email_service = get_email_service()

        asyncio.get_event_loop().run_until_complete(
            email_service.send_analysis_failed(
                user_email=owner.email,
                repo_name=repo.full_name,
                error_message=error_message,
            )
        )
    except Exception as e:
        logger.exception(f"Failed to send failure notification: {e}")


def _format_pr_comment(analysis: AnalysisRun) -> str:
    """Format analysis results as a GitHub PR comment.

    Args:
        analysis: AnalysisRun model instance.

    Returns:
        Markdown formatted comment body.
    """
    # Determine emoji based on score
    score = analysis.health_score or 0
    if score >= 70:
        emoji = "white_check_mark"
    elif score >= 50:
        emoji = "warning"
    else:
        emoji = "x"

    # Format score delta
    delta_str = ""
    if analysis.score_delta is not None:
        if analysis.score_delta > 0:
            delta_str = f" (:chart_with_upwards_trend: +{analysis.score_delta})"
        elif analysis.score_delta < 0:
            delta_str = f" (:chart_with_downwards_trend: {analysis.score_delta})"
        else:
            delta_str = " (no change)"

    return f"""## :{emoji}: Repotoire Code Health Analysis

**Health Score:** {score}/100{delta_str}

| Category | Score |
|----------|-------|
| Structure | {analysis.structure_score or '-'}/100 |
| Quality | {analysis.quality_score or '-'}/100 |
| Architecture | {analysis.architecture_score or '-'}/100 |

**Files Analyzed:** {analysis.files_analyzed}
**Issues Found:** {analysis.findings_count}

[View Full Report]({APP_BASE_URL}/analysis/{analysis.id})

---
*Powered by [Repotoire](https://repotoire.io) - Graph-Powered Code Health*
"""


def _get_github_token(org: Organization) -> str | None:
    """Get GitHub token for organization.

    Args:
        org: Organization model instance.

    Returns:
        GitHub token or None.
    """
    # Use environment variable for now
    return os.environ.get("GITHUB_TOKEN")


def _create_pr_comment(
    github_token: str,
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
) -> str | None:
    """Create a comment on a GitHub PR.

    Args:
        github_token: GitHub API token.
        owner: Repository owner.
        repo: Repository name.
        pr_number: Pull request number.
        body: Comment body (markdown).

    Returns:
        Comment ID or None if failed.
    """
    try:
        import httpx

        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"

        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"body": body},
            )

            if response.is_success:
                return str(response.json().get("id"))
            else:
                logger.error(
                    f"Failed to create PR comment: {response.status_code} {response.text}"
                )
                return None

    except Exception as e:
        logger.exception(f"Failed to create PR comment: {e}")
        return None


# =============================================================================
# Customer Webhook Delivery
# =============================================================================


@celery_app.task(
    name="repotoire.workers.hooks.send_webhook_to_customer",
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
    """Deliver a webhook to a customer-configured endpoint.

    Implements secure webhook delivery with:
    - HMAC signature for payload verification
    - Retry with exponential backoff
    - Delivery status tracking

    Args:
        webhook_id: Webhook configuration ID.
        event_type: Event type (e.g., "analysis.completed").
        payload: Event payload to deliver.

    Returns:
        dict with delivery status.
    """
    log = logger.bind(
        webhook_id=webhook_id,
        event_type=event_type,
    )

    with get_sync_session() as session:
        # Get webhook configuration
        webhook = _get_customer_webhook(session, webhook_id)
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
                _record_webhook_delivery(
                    session=session,
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
            _record_webhook_delivery(
                session=session,
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
    name="repotoire.workers.hooks.send_weekly_digest",
    soft_time_limit=300,
    time_limit=360,
)
def send_weekly_digest() -> dict[str, Any]:
    """Send weekly digest emails to all users with activity.

    This is a periodic task that runs every Monday at 9 AM UTC.

    Returns:
        dict with digest send statistics.
    """
    log = logger.bind(task="weekly_digest")
    log.info("starting_weekly_digest")

    sent_count = 0
    failed_count = 0
    skipped_count = 0

    with get_sync_session() as session:
        # Get users with repository activity in the past week
        one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        users = _get_users_with_activity(session, since=one_week_ago)

        for user in users:
            try:
                # Check if user has weekly digest enabled
                if user.email_preferences and not user.email_preferences.weekly_digest:
                    skipped_count += 1
                    continue

                # Get user's repository summary
                repos_summary = _get_user_repos_summary(
                    session, str(user.id), since=one_week_ago
                )

                if not repos_summary:
                    skipped_count += 1
                    continue  # No activity to report

                # Send digest email
                _send_digest_email(user, repos_summary)
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
        skipped_count=skipped_count,
    )

    return {
        "status": "completed",
        "sent_count": sent_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
    }


# =============================================================================
# Customer Webhook Helpers
# =============================================================================


def _get_customer_webhook(session, webhook_id: str):
    """Get a customer webhook configuration by ID.

    Args:
        session: Database session.
        webhook_id: Webhook configuration ID.

    Returns:
        Webhook model instance or None.
    """
    # Import here to avoid circular imports
    try:
        from repotoire.db.models import Webhook

        return session.get(Webhook, UUID(webhook_id))
    except ImportError:
        # Webhook model may not exist yet
        logger.warning("Webhook model not available")
        return None


def _generate_webhook_signature(payload: dict[str, Any], secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload.

    Args:
        payload: Webhook payload to sign.
        secret: Webhook secret key.

    Returns:
        Signature string in format "sha256=<hex>".
    """
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


def _is_valid_webhook_url(url: str) -> bool:
    """Validate webhook URL (prevent SSRF).

    Args:
        url: URL to validate.

    Returns:
        True if URL is safe to use, False otherwise.
    """
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


def _record_webhook_delivery(
    session,
    webhook_id: str,
    delivery_id: str,
    event_type: str,
    status_code: int,
    success: bool,
    error: str | None = None,
) -> None:
    """Record a webhook delivery attempt.

    Args:
        session: Database session.
        webhook_id: Webhook configuration ID.
        delivery_id: Unique delivery ID.
        event_type: Event type delivered.
        status_code: HTTP status code received.
        success: Whether delivery was successful.
        error: Error message if failed.
    """
    # Import here to avoid circular imports
    try:
        from repotoire.db.models import WebhookDelivery

        delivery = WebhookDelivery(
            webhook_id=UUID(webhook_id),
            delivery_id=delivery_id,
            event_type=event_type,
            status_code=status_code,
            success=success,
            error=error,
            delivered_at=datetime.now(timezone.utc),
        )
        session.add(delivery)
    except ImportError:
        # WebhookDelivery model may not exist yet
        logger.debug("WebhookDelivery model not available, skipping record")


# =============================================================================
# Weekly Digest Helpers
# =============================================================================


def _get_users_with_activity(session, since: datetime) -> list["User"]:
    """Get users with repository activity since a given date.

    Args:
        session: Database session.
        since: Datetime to check activity from.

    Returns:
        List of User model instances.
    """
    from repotoire.db.models import User

    # Find users who own repositories with analyses in the time period
    result = session.execute(
        select(User)
        .distinct()
        .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .join(Repository, Repository.organization_id == Organization.id)
        .join(AnalysisRun, AnalysisRun.repository_id == Repository.id)
        .where(AnalysisRun.created_at >= since)
        .where(OrganizationMembership.role == MemberRole.OWNER.value)
    )
    return list(result.scalars().all())


def _get_user_repos_summary(
    session,
    user_id: str,
    since: datetime,
) -> list[dict[str, Any]]:
    """Get summary of user's repositories with recent activity.

    Args:
        session: Database session.
        user_id: User ID.
        since: Datetime to check activity from.

    Returns:
        List of repository summary dicts.
    """
    from repotoire.db.models import User

    user = session.get(User, UUID(user_id))
    if not user:
        return []

    # Get organizations where user is owner
    memberships = session.execute(
        select(OrganizationMembership)
        .where(OrganizationMembership.user_id == user.id)
        .where(OrganizationMembership.role == MemberRole.OWNER.value)
    ).scalars().all()

    repos_summary = []
    for membership in memberships:
        org = session.get(Organization, membership.organization_id)
        if not org:
            continue

        # Get repositories with recent analyses
        repos_result = session.execute(
            select(Repository)
            .where(Repository.organization_id == org.id)
            .where(Repository.is_active == True)
        )

        for repo in repos_result.scalars().all():
            # Get latest analysis
            latest = session.execute(
                select(AnalysisRun)
                .where(AnalysisRun.repository_id == repo.id)
                .where(AnalysisRun.status == AnalysisStatus.COMPLETED)
                .order_by(AnalysisRun.completed_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            if not latest or latest.completed_at < since:
                continue

            # Get previous analysis for trend
            previous = session.execute(
                select(AnalysisRun)
                .where(AnalysisRun.repository_id == repo.id)
                .where(AnalysisRun.id != latest.id)
                .where(AnalysisRun.status == AnalysisStatus.COMPLETED)
                .order_by(AnalysisRun.completed_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            score = latest.health_score or 0
            trend = 0
            if previous and previous.health_score:
                trend = score - previous.health_score

            repos_summary.append({
                "name": repo.full_name,
                "grade": _score_to_grade(score),
                "score": score,
                "trend": trend,
            })

    return repos_summary


def _score_to_grade(score: float) -> str:
    """Convert health score to letter grade.

    Args:
        score: Health score (0-100).

    Returns:
        Letter grade (A-F).
    """
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


def _send_digest_email(user: "User", repos_summary: list[dict[str, Any]]) -> None:
    """Send weekly digest email to user.

    Args:
        user: User to send email to.
        repos_summary: List of repository summaries.
    """
    try:
        from repotoire.services.email import get_email_service
        import asyncio

        email_service = get_email_service()
        dashboard_url = f"{APP_BASE_URL}/dashboard"
        user_name = user.name or user.email.split("@")[0]

        # Build digest content
        subject = "[Repotoire] Your Weekly Code Health Digest"
        html_body = _build_digest_email_html(user_name, repos_summary, dashboard_url)

        # Run async email send in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                email_service.send_email(
                    to_email=user.email,
                    subject=subject,
                    html_body=html_body,
                )
            )
        finally:
            loop.close()

    except Exception as e:
        logger.exception(f"Failed to send digest email: {e}")
        raise


def _build_digest_email_html(
    user_name: str,
    repos_summary: list[dict[str, Any]],
    dashboard_url: str,
) -> str:
    """Build HTML email for weekly digest.

    Args:
        user_name: User's display name.
        repos_summary: List of repository summaries.
        dashboard_url: Dashboard URL.

    Returns:
        HTML email body.
    """
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
        trend_icon = "^" if trend > 0 else "v" if trend < 0 else "-"
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
                <a href="{APP_BASE_URL}/settings/notifications">Manage preferences</a>
            </p>
        </div>
    </body>
    </html>
    """

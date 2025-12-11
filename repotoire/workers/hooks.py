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
from repotoire.db.models.finding import Finding as FindingDB
from repotoire.db.models.finding import FindingSeverity
from repotoire.db.models.fix import Fix as FixDB
from repotoire.db.models.fix import FixConfidence as FixConfidenceDB
from repotoire.db.models.fix import FixStatus as FixStatusDB
from repotoire.db.models.fix import FixType as FixTypeDB
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

            # Trigger AI fix generation for high-severity findings
            # This runs asynchronously so users can start reviewing findings immediately
            generate_fixes_for_analysis.delay(
                analysis_run_id=analysis_run_id,
                max_fixes=10,
                severity_filter=["critical", "high"],
            )
            log = logger.bind(analysis_run_id=analysis_run_id)
            log.info("triggered_fix_generation")

            # Trigger customer webhooks for analysis.completed event
            _trigger_analysis_completed_webhook(
                org_id=org.id,
                analysis=analysis,
                repo=repo,
            )

            return {
                "status": "notified",
                "notification_type": "analysis_complete",
                "fix_generation": "triggered",
            }

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

            # Trigger customer webhooks for analysis.failed event
            _trigger_analysis_failed_webhook(
                org_id=org.id,
                analysis=analysis,
                repo=repo,
                error_message=error_message,
            )

            if owner.email_preferences is None or owner.email_preferences.analysis_failed:
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
    base_sha: str | None = None,
) -> dict:
    """Post analysis results as a PR comment.

    Creates a formatted comment on the pull request with:
    - Health score and score delta
    - NEW issues only (not pre-existing in base branch)
    - Grouped by severity
    - Link to full report

    Uses unique marker to update existing comments (avoids duplicates).

    Args:
        repo_id: UUID of the Repository.
        pr_number: Pull request number.
        analysis_run_id: UUID of the AnalysisRun.
        base_sha: Base commit SHA for comparison (optional).

    Returns:
        dict with status and comment_id if posted.
    """
    log = logger.bind(
        repo_id=repo_id,
        pr_number=pr_number,
        analysis_run_id=analysis_run_id,
    )

    try:
        from repotoire.github.pr_commenter import (
            format_pr_comment,
            get_base_analysis,
            get_installation_token_for_repo,
            get_new_findings,
            post_or_update_pr_comment,
        )

        with get_sync_session() as session:
            analysis = session.get(AnalysisRun, UUID(analysis_run_id))
            if not analysis:
                log.warning("analysis_not_found")
                return {"status": "skipped", "reason": "analysis_not_found"}

            if analysis.status != AnalysisStatus.COMPLETED:
                log.warning("analysis_not_completed", status=analysis.status)
                return {"status": "skipped", "reason": "analysis_not_completed"}

            repo = session.get(Repository, UUID(repo_id))
            if not repo:
                log.warning("repo_not_found")
                return {"status": "skipped", "reason": "repo_not_found"}

            # Get base analysis for comparison (to find NEW issues)
            base_analysis = get_base_analysis(session, repo.id, base_sha)
            base_analysis_id = base_analysis.id if base_analysis else None
            base_score = base_analysis.health_score if base_analysis else None

            # Find NEW findings (not in base)
            new_findings = get_new_findings(
                session=session,
                head_analysis_id=analysis.id,
                base_analysis_id=base_analysis_id,
            )

            log.info(
                "found_new_findings",
                total_findings=analysis.findings_count,
                new_findings=len(new_findings),
            )

            # Format comment
            dashboard_url = f"{APP_BASE_URL}/repos/{repo.id}/analysis/{analysis.id}"
            comment_body = format_pr_comment(
                analysis=analysis,
                new_findings=new_findings,
                base_score=base_score,
                dashboard_url=dashboard_url,
            )

            # Parse owner/repo from full_name
            parts = repo.full_name.split("/")
            if len(parts) != 2:
                log.error("invalid_repo_name", full_name=repo.full_name)
                return {"status": "skipped", "reason": "invalid_repo_name"}

            owner, repo_name = parts

            # Get GitHub token
            github_token = get_installation_token_for_repo(repo.id)
            if not github_token:
                log.warning("no_github_token")
                return {"status": "skipped", "reason": "no_github_token"}

            # Post or update comment
            result = post_or_update_pr_comment(
                owner=owner,
                repo=repo_name,
                pr_number=pr_number,
                body=comment_body,
                installation_token=github_token,
            )

            log.info(
                "pr_comment_posted",
                action=result.get("action"),
                comment_id=result.get("comment_id"),
            )

            return {
                "status": "posted",
                "action": result.get("action"),
                "comment_id": result.get("comment_id"),
                "url": result.get("url"),
                "pr_number": pr_number,
                "new_findings_count": len(new_findings),
            }

    except Exception as e:
        log.exception("post_pr_comment_failed", error=str(e))
        # Don't fail the task - PR comments are non-critical
        return {"status": "error", "error": str(e)}


# =============================================================================
# Commit Status Checks
# =============================================================================


@celery_app.task(name="repotoire.workers.hooks.set_commit_status_pending")
def set_commit_status_pending(
    repo_id: str,
    commit_sha: str,
    analysis_run_id: str,
) -> dict:
    """Set pending commit status when analysis starts.

    Args:
        repo_id: UUID of the Repository.
        commit_sha: Git commit SHA.
        analysis_run_id: UUID of the AnalysisRun.

    Returns:
        dict with status.
    """
    log = logger.bind(
        repo_id=repo_id,
        commit_sha=commit_sha[:8],
        analysis_run_id=analysis_run_id,
    )

    try:
        from repotoire.github.pr_commenter import get_installation_token_for_repo
        from repotoire.services.github_status import (
            CommitState,
            build_analysis_url,
            set_commit_status,
        )

        with get_sync_session() as session:
            repo = session.get(Repository, UUID(repo_id))
            if not repo:
                log.warning("repo_not_found")
                return {"status": "skipped", "reason": "repo_not_found"}

            # Get GitHub token
            token = get_installation_token_for_repo(repo.id)
            if not token:
                log.warning("no_github_token")
                return {"status": "skipped", "reason": "no_github_token"}

            # Set pending status
            target_url = build_analysis_url(analysis_run_id, repo_id)
            result = set_commit_status(
                installation_token=token,
                repo_full_name=repo.full_name,
                sha=commit_sha,
                state=CommitState.PENDING,
                description="Repotoire analysis in progress...",
                target_url=target_url,
            )

            if result:
                log.info("commit_status_pending_set")
                return {"status": "set", "state": "pending"}
            else:
                log.warning("commit_status_failed")
                return {"status": "failed"}

    except Exception as e:
        log.exception("set_commit_status_pending_error", error=str(e))
        return {"status": "error", "error": str(e)}


@celery_app.task(name="repotoire.workers.hooks.set_commit_status_result")
def set_commit_status_result(
    repo_id: str,
    commit_sha: str,
    analysis_run_id: str,
    base_sha: str | None = None,
) -> dict:
    """Set final commit status based on quality gate evaluation.

    Evaluates quality gates and sets success/failure status on the commit.
    This can block PR merges when configured as a required check.

    Args:
        repo_id: UUID of the Repository.
        commit_sha: Git commit SHA.
        analysis_run_id: UUID of the AnalysisRun.
        base_sha: Base commit SHA for finding new issues (optional).

    Returns:
        dict with status and quality gate result.
    """
    log = logger.bind(
        repo_id=repo_id,
        commit_sha=commit_sha[:8],
        analysis_run_id=analysis_run_id,
    )

    try:
        from repotoire.db.models import GitHubRepository
        from repotoire.github.pr_commenter import (
            get_base_analysis,
            get_installation_token_for_repo,
        )
        from repotoire.services.github_status import build_analysis_url, set_commit_status
        from repotoire.services.quality_gates import evaluate_quality_gates

        with get_sync_session() as session:
            # Get analysis run
            analysis = session.get(AnalysisRun, UUID(analysis_run_id))
            if not analysis:
                log.warning("analysis_not_found")
                return {"status": "skipped", "reason": "analysis_not_found"}

            if analysis.status != AnalysisStatus.COMPLETED:
                log.warning("analysis_not_completed", status=analysis.status)
                return {"status": "skipped", "reason": "analysis_not_completed"}

            # Get repository
            repo = session.get(Repository, UUID(repo_id))
            if not repo:
                log.warning("repo_not_found")
                return {"status": "skipped", "reason": "repo_not_found"}

            # Find GitHubRepository to get quality gates config
            result = session.execute(
                select(GitHubRepository).where(
                    GitHubRepository.repo_id == repo.github_repo_id
                )
            )
            github_repo = result.scalar_one_or_none()

            quality_gates = github_repo.quality_gates if github_repo else None

            # Get base analysis for comparison (for PR analysis)
            base_analysis = get_base_analysis(session, repo.id, base_sha) if base_sha else None
            base_analysis_id = base_analysis.id if base_analysis else None

            # Evaluate quality gates
            gate_result = evaluate_quality_gates(
                session=session,
                quality_gates=quality_gates,
                analysis_run=analysis,
                new_findings_only=bool(base_sha),
                base_analysis_id=base_analysis_id,
            )

            log.info(
                "quality_gates_evaluated",
                passed=gate_result.passed,
                state=gate_result.state.value,
                description=gate_result.description,
            )

            # Get GitHub token
            token = get_installation_token_for_repo(repo.id)
            if not token:
                log.warning("no_github_token")
                return {
                    "status": "skipped",
                    "reason": "no_github_token",
                    "gate_result": {
                        "passed": gate_result.passed,
                        "description": gate_result.description,
                    },
                }

            # Set commit status
            target_url = build_analysis_url(analysis_run_id, repo_id)
            result = set_commit_status(
                installation_token=token,
                repo_full_name=repo.full_name,
                sha=commit_sha,
                state=gate_result.state,
                description=gate_result.description,
                target_url=target_url,
            )

            if result:
                log.info(
                    "commit_status_result_set",
                    state=gate_result.state.value,
                    passed=gate_result.passed,
                )
                return {
                    "status": "set",
                    "state": gate_result.state.value,
                    "passed": gate_result.passed,
                    "description": gate_result.description,
                    "details": gate_result.details,
                }
            else:
                log.warning("commit_status_failed")
                return {"status": "failed", "gate_result": gate_result.details}

    except Exception as e:
        log.exception("set_commit_status_result_error", error=str(e))
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
        import asyncio

        from repotoire.services.email import get_email_service

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
        import asyncio

        from repotoire.services.email import get_email_service

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
        import asyncio

        from repotoire.services.email import get_email_service

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
        import asyncio

        from repotoire.services.email import get_email_service

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


# =============================================================================
# Customer Webhook Trigger Helpers
# =============================================================================


def _trigger_analysis_started_webhook(
    org_id: UUID,
    analysis_run_id: UUID,
    repo_id: UUID,
    repo_full_name: str,
    commit_sha: str,
    triggered_by: str = "api",
) -> None:
    """Trigger analysis.started webhooks for the organization.

    Args:
        org_id: Organization UUID.
        analysis_run_id: AnalysisRun UUID.
        repo_id: Repository UUID.
        repo_full_name: Repository full name (e.g., "owner/repo").
        commit_sha: Git commit SHA being analyzed.
        triggered_by: What triggered the analysis (e.g., "push", "pr", "manual").
    """
    try:
        from repotoire.services.webhook_payloads import build_analysis_started_payload
        from repotoire.workers.webhook_delivery import trigger_webhook_event

        payload = build_analysis_started_payload(
            analysis_run_id=analysis_run_id,
            repository_id=repo_id,
            repository_name=repo_full_name,
            commit_sha=commit_sha,
            triggered_by=triggered_by,
        )

        trigger_webhook_event.delay(
            organization_id=str(org_id),
            event_type="analysis.started",
            payload=payload,
            repository_id=str(repo_id),
        )

        logger.debug(
            "triggered_analysis_started_webhook",
            extra={"org_id": str(org_id), "analysis_run_id": str(analysis_run_id)},
        )

    except Exception as e:
        # Don't fail the main task if webhook triggering fails
        logger.warning(
            f"Failed to trigger analysis.started webhook: {e}",
            extra={"org_id": str(org_id), "analysis_run_id": str(analysis_run_id)},
        )


def _trigger_analysis_completed_webhook(
    org_id: UUID,
    analysis: AnalysisRun,
    repo: Repository,
) -> None:
    """Trigger analysis.completed webhooks for the organization.

    Args:
        org_id: Organization UUID.
        analysis: Completed AnalysisRun instance.
        repo: Repository instance.
    """
    try:
        from repotoire.services.webhook_payloads import build_analysis_completed_payload
        from repotoire.workers.webhook_delivery import trigger_webhook_event

        # Build finding counts by severity
        finding_counts: dict[str, int] = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
        }

        # Count findings if available
        if analysis.findings:
            for finding in analysis.findings:
                severity_key = finding.severity.value if finding.severity else "info"
                if severity_key in finding_counts:
                    finding_counts[severity_key] += 1

        # Calculate duration
        duration_seconds = 0
        if analysis.started_at and analysis.completed_at:
            duration_seconds = int(
                (analysis.completed_at - analysis.started_at).total_seconds()
            )

        payload = build_analysis_completed_payload(
            analysis_run_id=analysis.id,
            repository_id=repo.id,
            repository_name=repo.full_name,
            commit_sha=analysis.commit_sha or "",
            health_score=analysis.health_score or 0,
            finding_counts=finding_counts,
            duration_seconds=duration_seconds,
            structure_score=analysis.structure_score,
            quality_score=analysis.quality_score,
            architecture_score=analysis.architecture_score,
        )

        trigger_webhook_event.delay(
            organization_id=str(org_id),
            event_type="analysis.completed",
            payload=payload,
            repository_id=str(repo.id),
        )

        logger.debug(
            "triggered_analysis_completed_webhook",
            extra={"org_id": str(org_id), "analysis_id": str(analysis.id)},
        )

    except Exception as e:
        # Don't fail the main task if webhook triggering fails
        logger.warning(
            f"Failed to trigger analysis.completed webhook: {e}",
            extra={"org_id": str(org_id), "analysis_id": str(analysis.id)},
        )


def _trigger_analysis_failed_webhook(
    org_id: UUID,
    analysis: AnalysisRun,
    repo: Repository,
    error_message: str,
) -> None:
    """Trigger analysis.failed webhooks for the organization.

    Args:
        org_id: Organization UUID.
        analysis: Failed AnalysisRun instance.
        repo: Repository instance.
        error_message: Error description.
    """
    try:
        from repotoire.services.webhook_payloads import build_analysis_failed_payload
        from repotoire.workers.webhook_delivery import trigger_webhook_event

        payload = build_analysis_failed_payload(
            analysis_run_id=analysis.id,
            repository_id=repo.id,
            repository_name=repo.full_name,
            commit_sha=analysis.commit_sha or "",
            error_message=error_message,
        )

        trigger_webhook_event.delay(
            organization_id=str(org_id),
            event_type="analysis.failed",
            payload=payload,
            repository_id=str(repo.id),
        )

        logger.debug(
            "triggered_analysis_failed_webhook",
            extra={"org_id": str(org_id), "analysis_id": str(analysis.id)},
        )

    except Exception as e:
        # Don't fail the main task if webhook triggering fails
        logger.warning(
            f"Failed to trigger analysis.failed webhook: {e}",
            extra={"org_id": str(org_id), "analysis_id": str(analysis.id)},
        )


# =============================================================================
# AI Fix Generation
# =============================================================================


@celery_app.task(
    name="repotoire.workers.hooks.generate_fixes_for_analysis",
    soft_time_limit=600,  # 10 minutes
    time_limit=660,
    max_retries=1,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def generate_fixes_for_analysis(
    analysis_run_id: str,
    max_fixes: int = 10,
    severity_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Generate AI fixes for findings from an analysis run.

    Uses the AutoFixEngine to generate code fix proposals for high-priority
    findings. Fixes are stored in the database for human review.

    Args:
        analysis_run_id: UUID of the AnalysisRun with findings.
        max_fixes: Maximum number of fixes to generate (default 10).
        severity_filter: Optional list of severities to process (default: critical, high).

    Returns:
        dict with status, fixes_generated count, and errors.
    """
    import asyncio
    import os
    from pathlib import Path

    log = logger.bind(
        task="generate_fixes",
        analysis_run_id=analysis_run_id,
    )
    log.info("starting_fix_generation", max_fixes=max_fixes)

    # Check if Anthropic API key is available (prefer Claude Opus 4.5 for fix generation)
    if not os.getenv("ANTHROPIC_API_KEY"):
        log.warning("anthropic_api_key_missing")
        return {
            "status": "skipped",
            "reason": "ANTHROPIC_API_KEY not configured",
        }

    severity_filter = severity_filter or ["critical", "high"]
    fixes_generated = 0
    errors: list[str] = []

    try:
        with get_sync_session() as session:
            # Get analysis run and repository
            analysis = session.get(AnalysisRun, UUID(analysis_run_id))
            if not analysis:
                log.warning("analysis_not_found")
                return {"status": "skipped", "reason": "analysis_not_found"}

            if analysis.status != AnalysisStatus.COMPLETED:
                log.warning("analysis_not_completed", status=analysis.status)
                return {"status": "skipped", "reason": "analysis_not_completed"}

            repo = analysis.repository
            repo_full_name = repo.full_name

            # Get findings to process
            severity_enums = [
                FindingSeverity(s) for s in severity_filter
                if s in [e.value for e in FindingSeverity]
            ]

            findings_query = (
                select(FindingDB)
                .where(FindingDB.analysis_run_id == analysis.id)
                .where(FindingDB.severity.in_(severity_enums))
                .order_by(FindingDB.severity.asc())  # Critical first
                .limit(max_fixes)
            )
            findings_result = session.execute(findings_query)
            db_findings = list(findings_result.scalars().all())

            if not db_findings:
                log.info("no_findings_to_fix")
                return {
                    "status": "completed",
                    "fixes_generated": 0,
                    "reason": "no_high_severity_findings",
                }

            log.info("found_findings", count=len(db_findings))

        # Initialize AutoFixEngine with Claude Opus 4.5 (outside session to avoid long transactions)
        from repotoire.autofix.engine import AutoFixEngine
        from repotoire.graph.client import Neo4jClient
        from repotoire.models import Finding as ModelFinding
        from repotoire.models import Severity

        neo4j_uri = os.environ.get("REPOTOIRE_NEO4J_URI", "bolt://localhost:7687")
        neo4j_password = os.environ.get("REPOTOIRE_NEO4J_PASSWORD", "")
        neo4j_client = Neo4jClient(uri=neo4j_uri, password=neo4j_password)

        engine = AutoFixEngine(
            neo4j_client=neo4j_client,
            llm_backend="anthropic",  # Use Claude Opus 4.5 for best fix quality
            skip_runtime_validation=True,  # Skip sandbox validation for speed
        )

        # Clone repository to temp location for fix generation
        clone_dir = _clone_for_fixes(repo_full_name)
        if not clone_dir:
            log.error("clone_failed")
            return {"status": "failed", "reason": "could_not_clone_repository"}

        try:
            # Generate fixes for each finding
            for db_finding in db_findings:
                try:
                    # Convert DB finding to model finding
                    severity_map = {
                        FindingSeverity.CRITICAL: Severity.CRITICAL,
                        FindingSeverity.HIGH: Severity.HIGH,
                        FindingSeverity.MEDIUM: Severity.MEDIUM,
                        FindingSeverity.LOW: Severity.LOW,
                        FindingSeverity.INFO: Severity.INFO,
                    }

                    model_finding = ModelFinding(
                        id=str(db_finding.id),
                        detector=db_finding.detector,
                        severity=severity_map.get(db_finding.severity, Severity.MEDIUM),
                        title=db_finding.title,
                        description=db_finding.description or "",
                        affected_nodes=db_finding.affected_nodes or [],
                        affected_files=db_finding.affected_files or [],
                        line_start=db_finding.line_start,
                        line_end=db_finding.line_end,
                        graph_context=db_finding.graph_context or {},
                        suggested_fix=db_finding.suggested_fix,
                        estimated_effort=db_finding.estimated_effort,
                    )

                    # Generate fix using async engine
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        fix_proposal = loop.run_until_complete(
                            engine.generate_fix(
                                finding=model_finding,
                                repository_path=Path(clone_dir),
                                context_size=3,
                                skip_runtime_validation=True,
                            )
                        )
                    finally:
                        loop.close()

                    if fix_proposal is None:
                        log.debug("no_fix_generated", finding_id=str(db_finding.id))
                        continue

                    # Store fix in database
                    with get_sync_session() as session:
                        _store_fix(
                            session=session,
                            analysis_run_id=UUID(analysis_run_id),
                            finding_id=db_finding.id,
                            fix_proposal=fix_proposal,
                        )

                    fixes_generated += 1
                    log.info(
                        "fix_generated",
                        finding_id=str(db_finding.id),
                        fix_type=fix_proposal.fix_type.value,
                        confidence=fix_proposal.confidence.value,
                    )

                except Exception as e:
                    log.warning(
                        "fix_generation_failed",
                        finding_id=str(db_finding.id),
                        error=str(e),
                    )
                    errors.append(f"Finding {db_finding.id}: {str(e)[:100]}")

        finally:
            # Cleanup clone directory
            import shutil
            if clone_dir and Path(clone_dir).exists():
                shutil.rmtree(clone_dir, ignore_errors=True)

        log.info(
            "fix_generation_complete",
            fixes_generated=fixes_generated,
            errors_count=len(errors),
        )

        return {
            "status": "completed",
            "fixes_generated": fixes_generated,
            "errors": errors[:5],  # Limit error messages
        }

    except Exception as e:
        log.exception("fix_generation_error", error=str(e))
        return {
            "status": "failed",
            "error": str(e),
            "fixes_generated": fixes_generated,
        }


def _clone_for_fixes(full_name: str) -> str | None:
    """Clone repository for fix generation.

    Args:
        full_name: Repository full name (owner/repo).

    Returns:
        Path to clone directory or None if failed.
    """
    import subprocess
    import tempfile
    from pathlib import Path

    try:
        # Create temp directory
        clone_dir = Path(tempfile.mkdtemp(prefix="repotoire-fixes-"))

        # Get GitHub token
        token = os.environ.get("GITHUB_TOKEN")
        clone_url = f"https://github.com/{full_name}.git"
        if token:
            clone_url = f"https://x-access-token:{token}@github.com/{full_name}.git"

        # Shallow clone for speed
        subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(clone_dir)],
            check=True,
            capture_output=True,
            timeout=120,
        )

        return str(clone_dir)

    except Exception as e:
        logger.warning(f"Clone failed: {e}")
        return None


def _store_fix(
    session,
    analysis_run_id: UUID,
    finding_id: UUID,
    fix_proposal,
) -> None:
    """Store a generated fix in the database.

    Args:
        session: Database session.
        analysis_run_id: Analysis run UUID.
        finding_id: Finding UUID.
        fix_proposal: FixProposal from AutoFixEngine.
    """
    from repotoire.autofix.models import FixConfidence, FixType

    # Map autofix types to DB types
    type_map = {
        FixType.REFACTOR: FixTypeDB.REFACTOR,
        FixType.SIMPLIFY: FixTypeDB.SIMPLIFY,
        FixType.EXTRACT: FixTypeDB.EXTRACT,
        FixType.RENAME: FixTypeDB.RENAME,
        FixType.REMOVE: FixTypeDB.REMOVE,
        FixType.SECURITY: FixTypeDB.SECURITY,
        FixType.TYPE_HINT: FixTypeDB.TYPE_HINT,
        FixType.DOCUMENTATION: FixTypeDB.DOCUMENTATION,
    }

    confidence_map = {
        FixConfidence.HIGH: FixConfidenceDB.HIGH,
        FixConfidence.MEDIUM: FixConfidenceDB.MEDIUM,
        FixConfidence.LOW: FixConfidenceDB.LOW,
    }

    # Get the first change (most fixes have one change)
    change = fix_proposal.changes[0] if fix_proposal.changes else None
    if not change:
        return

    # Build evidence dict
    evidence = {}
    if fix_proposal.evidence:
        evidence = {
            "similar_patterns": fix_proposal.evidence.similar_patterns or [],
            "documentation_refs": fix_proposal.evidence.documentation_refs or [],
            "best_practices": fix_proposal.evidence.best_practices or [],
            "rag_context_count": len(fix_proposal.evidence.rag_context or []),
        }

    # Build validation data
    validation_data = {
        "syntax_valid": fix_proposal.syntax_valid,
        "import_valid": fix_proposal.import_valid,
        "type_valid": fix_proposal.type_valid,
        "errors": fix_proposal.validation_errors or [],
        "warnings": fix_proposal.validation_warnings or [],
    }

    # Calculate numeric confidence score
    confidence_score_map = {
        FixConfidence.HIGH: 0.9,
        FixConfidence.MEDIUM: 0.7,
        FixConfidence.LOW: 0.5,
    }

    fix = FixDB(
        analysis_run_id=analysis_run_id,
        finding_id=finding_id,
        file_path=str(change.file_path),
        line_start=change.start_line,
        line_end=change.end_line,
        original_code=change.original_code,
        fixed_code=change.fixed_code,
        title=fix_proposal.title[:500],
        description=fix_proposal.description or "",
        explanation=fix_proposal.rationale or "",
        fix_type=type_map.get(fix_proposal.fix_type, FixTypeDB.REFACTOR),
        confidence=confidence_map.get(fix_proposal.confidence, FixConfidenceDB.MEDIUM),
        confidence_score=confidence_score_map.get(fix_proposal.confidence, 0.7),
        status=FixStatusDB.PENDING,
        evidence=evidence,
        validation_data=validation_data,
    )

    session.add(fix)
    session.commit()

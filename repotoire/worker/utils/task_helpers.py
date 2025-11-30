"""
Helper utilities for Celery tasks.

This module provides database access and common operations
used by task functions. All database operations use SQLAlchemy
sessions managed per-call to ensure thread safety.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID
import time

import structlog
import jwt
import httpx

logger = structlog.get_logger(__name__)

# GitHub App configuration for installation tokens
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
GITHUB_APP_PRIVATE_KEY = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_API_BASE = "https://api.github.com"


# =============================================================================
# Data Classes (used as return types when database models aren't available)
# =============================================================================


@dataclass
class RepositoryInfo:
    """Repository information from database."""

    id: UUID
    full_name: str
    owner_id: UUID | None
    installation_id: int | None
    default_branch: str
    auto_analyze_enabled: bool
    pr_analysis_enabled: bool
    clone_url: str
    latest_score: float | None = None
    score_trend: float | None = None


@dataclass
class UserInfo:
    """User information from database."""

    id: UUID
    email: str
    name: str | None
    email_notifications_enabled: bool


@dataclass
class AnalysisRunInfo:
    """Analysis run information from database."""

    id: UUID
    repository_id: UUID
    commit_sha: str
    status: str


@dataclass
class WebhookConfig:
    """Customer webhook configuration."""

    id: UUID
    url: str
    secret: str
    enabled: bool
    subscribed_events: list[str]


# =============================================================================
# Repository Operations
# =============================================================================


def get_repository_by_id(repo_id: str) -> RepositoryInfo | None:
    """
    Get repository by ID from PostgreSQL.

    Args:
        repo_id: Repository UUID as string

    Returns:
        RepositoryInfo or None if not found
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import Repository

    try:
        with get_session() as session:
            repo = session.query(Repository).filter(
                Repository.id == UUID(repo_id)
            ).first()

            if not repo:
                return None

            return RepositoryInfo(
                id=repo.id,
                full_name=repo.full_name,
                owner_id=repo.owner_id,
                installation_id=repo.installation_id,
                default_branch=repo.default_branch or "main",
                auto_analyze_enabled=repo.auto_analyze_enabled,
                pr_analysis_enabled=repo.pr_analysis_enabled,
                clone_url=repo.clone_url,
            )
    except Exception as exc:
        logger.exception("get_repository_by_id_failed", repo_id=repo_id, error=str(exc))
        return None


def get_repository_by_full_name(full_name: str) -> RepositoryInfo | None:
    """
    Get repository by full name (owner/repo) from PostgreSQL.

    Args:
        full_name: Repository full name (e.g., "owner/repo")

    Returns:
        RepositoryInfo or None if not found
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import Repository

    try:
        with get_session() as session:
            repo = session.query(Repository).filter(
                Repository.full_name == full_name,
                Repository.is_active == True,  # noqa: E712
            ).first()

            if not repo:
                return None

            return RepositoryInfo(
                id=repo.id,
                full_name=repo.full_name,
                owner_id=repo.owner_id,
                installation_id=repo.installation_id,
                default_branch=repo.default_branch or "main",
                auto_analyze_enabled=repo.auto_analyze_enabled,
                pr_analysis_enabled=repo.pr_analysis_enabled,
                clone_url=repo.clone_url,
            )
    except Exception as exc:
        logger.exception(
            "get_repository_by_full_name_failed",
            full_name=full_name,
            error=str(exc),
        )
        return None


def get_or_create_repository(
    full_name: str,
    installation_id: int,
    owner_login: str,
    owner_type: str,
    private: bool,
) -> RepositoryInfo:
    """
    Get existing repository or create new one.

    Args:
        full_name: Repository full name
        installation_id: GitHub App installation ID
        owner_login: Owner username/org name
        owner_type: "User" or "Organization"
        private: Whether repo is private

    Returns:
        RepositoryInfo for the repository
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import Repository

    with get_session() as session:
        # Try to find existing
        repo = session.query(Repository).filter(
            Repository.full_name == full_name
        ).first()

        if repo:
            # Update installation ID if changed
            if repo.installation_id != installation_id:
                repo.installation_id = installation_id
            repo.is_active = True
            session.commit()
        else:
            # Create new repository
            repo = Repository(
                full_name=full_name,
                installation_id=installation_id,
                owner_login=owner_login,
                owner_type=owner_type,
                private=private,
                clone_url=f"https://github.com/{full_name}.git",
                is_active=True,
                auto_analyze_enabled=True,
                pr_analysis_enabled=True,
            )
            session.add(repo)
            session.commit()
            session.refresh(repo)

        return RepositoryInfo(
            id=repo.id,
            full_name=repo.full_name,
            owner_id=repo.owner_id,
            installation_id=repo.installation_id,
            default_branch=repo.default_branch or "main",
            auto_analyze_enabled=repo.auto_analyze_enabled,
            pr_analysis_enabled=repo.pr_analysis_enabled,
            clone_url=repo.clone_url,
        )


def mark_repository_inactive(full_name: str) -> None:
    """
    Mark a repository as inactive (soft delete).

    Args:
        full_name: Repository full name
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import Repository

    with get_session() as session:
        repo = session.query(Repository).filter(
            Repository.full_name == full_name
        ).first()

        if repo:
            repo.is_active = False
            repo.installation_id = None
            session.commit()


# =============================================================================
# Analysis Run Operations
# =============================================================================


def create_analysis_run(
    repository_id: str,
    commit_sha: str,
    triggered_by: str,
    status: str,
) -> UUID:
    """
    Create a new analysis run record.

    Args:
        repository_id: Repository UUID
        commit_sha: Git commit SHA
        triggered_by: What triggered the analysis
        status: Initial status

    Returns:
        UUID of created analysis run
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import AnalysisRun

    with get_session() as session:
        run = AnalysisRun(
            repository_id=UUID(repository_id),
            commit_sha=commit_sha,
            triggered_by=triggered_by,
            status=status,
            started_at=datetime.utcnow(),
        )
        session.add(run)
        session.commit()
        return run.id


def update_analysis_status(
    analysis_run_id: UUID,
    status: str,
    health_score: float | None = None,
    findings_count: int | None = None,
    duration_seconds: float | None = None,
    results: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """
    Update analysis run status and results.

    Args:
        analysis_run_id: Analysis run UUID
        status: New status
        health_score: Optional health score
        findings_count: Optional findings count
        duration_seconds: Optional duration
        results: Optional detailed results dict
        error: Optional error message
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import AnalysisRun

    with get_session() as session:
        run = session.query(AnalysisRun).filter(
            AnalysisRun.id == analysis_run_id
        ).first()

        if run:
            run.status = status
            run.completed_at = datetime.utcnow()

            if health_score is not None:
                run.health_score = health_score
            if findings_count is not None:
                run.findings_count = findings_count
            if duration_seconds is not None:
                run.duration_seconds = duration_seconds
            if results is not None:
                run.results = results
            if error is not None:
                run.error = error

            session.commit()


def get_analysis_by_commit(
    repo_full_name: str,
    commit_sha: str,
) -> AnalysisRunInfo | None:
    """
    Get analysis run by repository and commit SHA.

    Args:
        repo_full_name: Repository full name
        commit_sha: Git commit SHA

    Returns:
        AnalysisRunInfo or None if not found
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import AnalysisRun, Repository

    try:
        with get_session() as session:
            run = (
                session.query(AnalysisRun)
                .join(Repository)
                .filter(
                    Repository.full_name == repo_full_name,
                    AnalysisRun.commit_sha == commit_sha,
                )
                .first()
            )

            if not run:
                return None

            return AnalysisRunInfo(
                id=run.id,
                repository_id=run.repository_id,
                commit_sha=run.commit_sha,
                status=run.status,
            )
    except Exception as exc:
        logger.exception(
            "get_analysis_by_commit_failed",
            repo=repo_full_name,
            commit=commit_sha,
            error=str(exc),
        )
        return None


# =============================================================================
# User Operations
# =============================================================================


def get_user_by_id(user_id: str) -> UserInfo | None:
    """
    Get user by ID from PostgreSQL.

    Args:
        user_id: User UUID as string

    Returns:
        UserInfo or None if not found
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import User

    try:
        with get_session() as session:
            user = session.query(User).filter(User.id == UUID(user_id)).first()

            if not user:
                return None

            return UserInfo(
                id=user.id,
                email=user.email,
                name=user.name,
                email_notifications_enabled=user.email_notifications_enabled,
            )
    except Exception as exc:
        logger.exception("get_user_by_id_failed", user_id=user_id, error=str(exc))
        return None


def get_users_with_activity(since: datetime) -> list[UserInfo]:
    """
    Get users with repository activity since a given date.

    Args:
        since: Only include users with activity after this date

    Returns:
        List of UserInfo objects
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import User, Repository, AnalysisRun

    try:
        with get_session() as session:
            # Find users who own repos with recent analysis runs
            users = (
                session.query(User)
                .join(Repository, Repository.owner_id == User.id)
                .join(AnalysisRun, AnalysisRun.repository_id == Repository.id)
                .filter(
                    AnalysisRun.completed_at >= since,
                    User.email_notifications_enabled == True,  # noqa: E712
                )
                .distinct()
                .all()
            )

            return [
                UserInfo(
                    id=user.id,
                    email=user.email,
                    name=user.name,
                    email_notifications_enabled=user.email_notifications_enabled,
                )
                for user in users
            ]
    except Exception as exc:
        logger.exception("get_users_with_activity_failed", error=str(exc))
        return []


def get_user_repositories_with_scores(
    user_id: str,
    since: datetime,
) -> list[RepositoryInfo]:
    """
    Get user's repositories with their latest health scores.

    Args:
        user_id: User UUID
        since: Only include repos with activity since this date

    Returns:
        List of RepositoryInfo with latest_score and score_trend populated
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import Repository, AnalysisRun
    from sqlalchemy import func, desc

    try:
        with get_session() as session:
            # Get repos with latest analysis
            repos_query = (
                session.query(Repository)
                .filter(
                    Repository.owner_id == UUID(user_id),
                    Repository.is_active == True,  # noqa: E712
                )
                .all()
            )

            results = []
            for repo in repos_query:
                # Get latest two analysis runs for trend calculation
                recent_runs = (
                    session.query(AnalysisRun)
                    .filter(
                        AnalysisRun.repository_id == repo.id,
                        AnalysisRun.status == "completed",
                    )
                    .order_by(desc(AnalysisRun.completed_at))
                    .limit(2)
                    .all()
                )

                latest_score = None
                score_trend = 0.0

                if recent_runs:
                    latest_score = recent_runs[0].health_score
                    if len(recent_runs) > 1 and recent_runs[1].health_score:
                        score_trend = (latest_score or 0) - recent_runs[1].health_score

                results.append(
                    RepositoryInfo(
                        id=repo.id,
                        full_name=repo.full_name,
                        owner_id=repo.owner_id,
                        installation_id=repo.installation_id,
                        default_branch=repo.default_branch or "main",
                        auto_analyze_enabled=repo.auto_analyze_enabled,
                        pr_analysis_enabled=repo.pr_analysis_enabled,
                        clone_url=repo.clone_url,
                        latest_score=latest_score,
                        score_trend=score_trend,
                    )
                )

            return results
    except Exception as exc:
        logger.exception(
            "get_user_repositories_with_scores_failed",
            user_id=user_id,
            error=str(exc),
        )
        return []


# =============================================================================
# GitHub Operations
# =============================================================================


def clone_repository(
    repo: RepositoryInfo,
    commit_sha: str,
    base_dir: Path,
) -> Path:
    """
    Clone a repository to a temporary directory.

    Uses GitHub App installation token for authentication.

    Args:
        repo: Repository info
        commit_sha: Commit SHA to checkout
        base_dir: Base directory for clones

    Returns:
        Path to cloned repository
    """
    # Ensure base directory exists
    base_dir.mkdir(parents=True, exist_ok=True)

    # Create unique clone directory
    clone_dir = base_dir / f"{repo.full_name.replace('/', '_')}_{commit_sha[:8]}"

    if clone_dir.exists():
        # Already cloned, just checkout the commit
        subprocess.run(
            ["git", "checkout", commit_sha],
            cwd=clone_dir,
            check=True,
            capture_output=True,
        )
        return clone_dir

    # Get installation token for authenticated clone
    token = get_github_token_for_repo(repo)

    if token:
        # Authenticated clone URL
        clone_url = f"https://x-access-token:{token}@github.com/{repo.full_name}.git"
    else:
        # Public repo or fallback
        clone_url = repo.clone_url

    # Clone with depth 1 for speed (fetch specific commit after)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            clone_url,
            str(clone_dir),
        ],
        check=True,
        capture_output=True,
    )

    # Fetch the specific commit if not HEAD
    subprocess.run(
        ["git", "fetch", "--depth", "1", "origin", commit_sha],
        cwd=clone_dir,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["git", "checkout", commit_sha],
        cwd=clone_dir,
        check=True,
        capture_output=True,
    )

    return clone_dir


def get_github_token_for_repo(repo: RepositoryInfo) -> str | None:
    """
    Get GitHub installation token for repository.

    Creates a JWT signed with the app's private key and exchanges
    it for an installation access token.

    Args:
        repo: Repository info with installation_id

    Returns:
        Installation access token or None
    """
    if not repo.installation_id or not GITHUB_APP_ID or not GITHUB_APP_PRIVATE_KEY:
        return None

    try:
        # Create JWT
        now = int(time.time())
        payload = {
            "iat": now - 60,  # Issued 60 seconds ago to account for clock drift
            "exp": now + 600,  # Expires in 10 minutes
            "iss": GITHUB_APP_ID,
        }

        jwt_token = jwt.encode(
            payload,
            GITHUB_APP_PRIVATE_KEY,
            algorithm="RS256",
        )

        # Exchange JWT for installation token
        url = f"{GITHUB_API_BASE}/app/installations/{repo.installation_id}/access_tokens"

        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {jwt_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )

            if response.is_success:
                return response.json().get("token")

    except Exception as exc:
        logger.exception(
            "get_github_token_failed",
            installation_id=repo.installation_id,
            error=str(exc),
        )

    return None


# =============================================================================
# Webhook Operations
# =============================================================================


def get_customer_webhook(webhook_id: str) -> WebhookConfig | None:
    """
    Get customer webhook configuration.

    Args:
        webhook_id: Webhook UUID

    Returns:
        WebhookConfig or None if not found
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import Webhook

    try:
        with get_session() as session:
            webhook = session.query(Webhook).filter(
                Webhook.id == UUID(webhook_id)
            ).first()

            if not webhook:
                return None

            return WebhookConfig(
                id=webhook.id,
                url=webhook.url,
                secret=webhook.secret,
                enabled=webhook.enabled,
                subscribed_events=webhook.subscribed_events or [],
            )
    except Exception as exc:
        logger.exception(
            "get_customer_webhook_failed",
            webhook_id=webhook_id,
            error=str(exc),
        )
        return None


def record_webhook_delivery(
    webhook_id: str,
    delivery_id: str,
    event_type: str,
    status_code: int,
    success: bool,
    error: str | None = None,
) -> None:
    """
    Record a webhook delivery attempt.

    Args:
        webhook_id: Webhook UUID
        delivery_id: Unique delivery ID
        event_type: Event type delivered
        status_code: HTTP status code (0 for timeout)
        success: Whether delivery succeeded
        error: Optional error message
    """
    from repotoire.db.session import get_session
    from repotoire.db.models import WebhookDelivery

    try:
        with get_session() as session:
            delivery = WebhookDelivery(
                webhook_id=UUID(webhook_id),
                delivery_id=delivery_id,
                event_type=event_type,
                status_code=status_code,
                success=success,
                error=error,
                delivered_at=datetime.utcnow(),
            )
            session.add(delivery)
            session.commit()
    except Exception as exc:
        logger.exception(
            "record_webhook_delivery_failed",
            webhook_id=webhook_id,
            delivery_id=delivery_id,
            error=str(exc),
        )

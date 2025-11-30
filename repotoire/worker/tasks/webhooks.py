"""
Webhook processing tasks for Repotoire.

This module contains tasks for processing GitHub webhooks:
- Push events (trigger analysis on default branch)
- Pull request events (trigger PR analysis)
- Installation events (setup/teardown repositories)
"""

from __future__ import annotations

from typing import Any
import structlog

from repotoire.worker.celery_app import celery_app
from repotoire.worker.utils.task_helpers import (
    get_repository_by_full_name,
    get_or_create_repository,
    mark_repository_inactive,
    get_analysis_by_commit,
)

logger = structlog.get_logger(__name__)


@celery_app.task(
    name="repotoire.worker.tasks.webhooks.process_push_event",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=5,
    soft_time_limit=60,
    time_limit=90,
)
def process_push_event(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process a GitHub push webhook event.

    This task is idempotent - safe to retry without side effects.
    Only triggers analysis for pushes to the default branch.

    Args:
        payload: GitHub push webhook payload

    Returns:
        Dictionary with processing status:
        - status: queued, skipped
        - reason: Why it was skipped (if applicable)
        - task_id: Analysis task ID (if queued)
    """
    # Extract push event data
    repo_full_name = payload.get("repository", {}).get("full_name")
    commit_sha = payload.get("after")
    ref = payload.get("ref", "")
    branch = ref.replace("refs/heads/", "") if ref.startswith("refs/heads/") else ref

    # Handle deleted branch (all zeros SHA)
    if commit_sha == "0000000000000000000000000000000000000000":
        return {"status": "skipped", "reason": "branch_deleted"}

    log = logger.bind(
        repo=repo_full_name,
        commit_sha=commit_sha,
        branch=branch,
    )

    # Validate payload
    if not repo_full_name or not commit_sha:
        log.warning("invalid_push_payload")
        return {"status": "skipped", "reason": "invalid_payload"}

    # Idempotency check: Skip if already processed
    existing = get_analysis_by_commit(repo_full_name, commit_sha)
    if existing:
        log.debug("push_already_processed")
        return {
            "status": "skipped",
            "reason": "already_processed",
            "analysis_id": str(existing.id),
        }

    # Find repository in database
    repo = get_repository_by_full_name(repo_full_name)
    if not repo:
        log.debug("repository_not_connected")
        return {"status": "skipped", "reason": "repo_not_connected"}

    # Only analyze default branch pushes automatically
    default_branch = payload.get("repository", {}).get("default_branch", "main")
    if branch != default_branch:
        log.debug("not_default_branch", default_branch=default_branch)
        return {"status": "skipped", "reason": "not_default_branch"}

    # Check if repository has auto-analysis enabled
    if not repo.auto_analyze_enabled:
        log.debug("auto_analyze_disabled")
        return {"status": "skipped", "reason": "auto_analyze_disabled"}

    # Queue analysis task
    from repotoire.worker.tasks.analysis import analyze_repository

    task = analyze_repository.delay(
        repo_id=str(repo.id),
        commit_sha=commit_sha,
        incremental=True,
        triggered_by="push",
    )

    log.info("analysis_queued", task_id=task.id)

    return {
        "status": "queued",
        "task_id": task.id,
        "commit_sha": commit_sha,
        "branch": branch,
    }


@celery_app.task(
    name="repotoire.worker.tasks.webhooks.process_pr_event",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=5,
    soft_time_limit=60,
    time_limit=90,
)
def process_pr_event(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process a GitHub pull request webhook event.

    Triggers PR analysis on opened, synchronize, and reopened actions.

    Args:
        payload: GitHub pull_request webhook payload

    Returns:
        Dictionary with processing status
    """
    action = payload.get("action")
    pr = payload.get("pull_request", {})
    pr_number = payload.get("number") or pr.get("number")
    repo_full_name = payload.get("repository", {}).get("full_name")

    log = logger.bind(
        action=action,
        pr_number=pr_number,
        repo=repo_full_name,
    )

    # Only analyze on specific actions
    analyzable_actions = {"opened", "synchronize", "reopened"}
    if action not in analyzable_actions:
        log.debug("pr_action_ignored")
        return {"status": "skipped", "reason": f"action_{action}_ignored"}

    # Validate PR data
    base_sha = pr.get("base", {}).get("sha")
    head_sha = pr.get("head", {}).get("sha")

    if not all([repo_full_name, pr_number, base_sha, head_sha]):
        log.warning("invalid_pr_payload")
        return {"status": "skipped", "reason": "invalid_payload"}

    # Find repository in database
    repo = get_repository_by_full_name(repo_full_name)
    if not repo:
        log.debug("repository_not_connected")
        return {"status": "skipped", "reason": "repo_not_connected"}

    # Check if PR analysis is enabled
    if not repo.pr_analysis_enabled:
        log.debug("pr_analysis_disabled")
        return {"status": "skipped", "reason": "pr_analysis_disabled"}

    # Queue PR analysis task
    from repotoire.worker.tasks.analysis import analyze_pull_request

    task = analyze_pull_request.delay(
        repo_id=str(repo.id),
        pr_number=pr_number,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    log.info("pr_analysis_queued", task_id=task.id)

    return {
        "status": "queued",
        "task_id": task.id,
        "pr_number": pr_number,
        "base_sha": base_sha,
        "head_sha": head_sha,
    }


@celery_app.task(
    name="repotoire.worker.tasks.webhooks.process_installation_event",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=3,
    soft_time_limit=120,
    time_limit=180,
)
def process_installation_event(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Process a GitHub App installation webhook event.

    Handles:
    - created: New installation, setup repositories
    - deleted: Installation removed, cleanup
    - added: Repositories added to existing installation
    - removed: Repositories removed from installation

    Args:
        payload: GitHub installation webhook payload

    Returns:
        Dictionary with processing status and affected repositories
    """
    action = payload.get("action")
    installation = payload.get("installation", {})
    installation_id = installation.get("id")
    sender = payload.get("sender", {}).get("login")

    log = logger.bind(
        action=action,
        installation_id=installation_id,
        sender=sender,
    )

    log.info("processing_installation_event")

    if action == "created":
        return _handle_installation_created(payload, log)
    elif action == "deleted":
        return _handle_installation_deleted(payload, log)
    elif action == "added":
        return _handle_repositories_added(payload, log)
    elif action == "removed":
        return _handle_repositories_removed(payload, log)
    else:
        log.debug("installation_action_ignored")
        return {"status": "skipped", "reason": f"action_{action}_ignored"}


def _handle_installation_created(
    payload: dict[str, Any],
    log: structlog.stdlib.BoundLogger,
) -> dict[str, Any]:
    """Handle new GitHub App installation."""
    installation = payload.get("installation", {})
    installation_id = installation.get("id")
    account = installation.get("account", {})
    repositories = payload.get("repositories", [])

    created_repos = []
    for repo_data in repositories:
        repo_full_name = repo_data.get("full_name")
        if not repo_full_name:
            continue

        try:
            repo = get_or_create_repository(
                full_name=repo_full_name,
                installation_id=installation_id,
                owner_login=account.get("login"),
                owner_type=account.get("type", "User"),
                private=repo_data.get("private", False),
            )
            created_repos.append(repo_full_name)
            log.info("repository_connected", repo=repo_full_name)
        except Exception as exc:
            log.warning(
                "repository_connect_failed",
                repo=repo_full_name,
                error=str(exc),
            )

    return {
        "status": "completed",
        "action": "created",
        "installation_id": installation_id,
        "repositories_connected": created_repos,
        "count": len(created_repos),
    }


def _handle_installation_deleted(
    payload: dict[str, Any],
    log: structlog.stdlib.BoundLogger,
) -> dict[str, Any]:
    """Handle GitHub App installation deletion."""
    installation = payload.get("installation", {})
    installation_id = installation.get("id")
    repositories = payload.get("repositories", [])

    deactivated_repos = []
    for repo_data in repositories:
        repo_full_name = repo_data.get("full_name")
        if not repo_full_name:
            continue

        try:
            mark_repository_inactive(repo_full_name)
            deactivated_repos.append(repo_full_name)
            log.info("repository_deactivated", repo=repo_full_name)
        except Exception as exc:
            log.warning(
                "repository_deactivate_failed",
                repo=repo_full_name,
                error=str(exc),
            )

    return {
        "status": "completed",
        "action": "deleted",
        "installation_id": installation_id,
        "repositories_deactivated": deactivated_repos,
        "count": len(deactivated_repos),
    }


def _handle_repositories_added(
    payload: dict[str, Any],
    log: structlog.stdlib.BoundLogger,
) -> dict[str, Any]:
    """Handle repositories added to existing installation."""
    installation = payload.get("installation", {})
    installation_id = installation.get("id")
    account = installation.get("account", {})
    repositories = payload.get("repositories_added", [])

    added_repos = []
    for repo_data in repositories:
        repo_full_name = repo_data.get("full_name")
        if not repo_full_name:
            continue

        try:
            repo = get_or_create_repository(
                full_name=repo_full_name,
                installation_id=installation_id,
                owner_login=account.get("login"),
                owner_type=account.get("type", "User"),
                private=repo_data.get("private", False),
            )
            added_repos.append(repo_full_name)
            log.info("repository_added", repo=repo_full_name)
        except Exception as exc:
            log.warning(
                "repository_add_failed",
                repo=repo_full_name,
                error=str(exc),
            )

    return {
        "status": "completed",
        "action": "added",
        "installation_id": installation_id,
        "repositories_added": added_repos,
        "count": len(added_repos),
    }


def _handle_repositories_removed(
    payload: dict[str, Any],
    log: structlog.stdlib.BoundLogger,
) -> dict[str, Any]:
    """Handle repositories removed from installation."""
    installation_id = payload.get("installation", {}).get("id")
    repositories = payload.get("repositories_removed", [])

    removed_repos = []
    for repo_data in repositories:
        repo_full_name = repo_data.get("full_name")
        if not repo_full_name:
            continue

        try:
            mark_repository_inactive(repo_full_name)
            removed_repos.append(repo_full_name)
            log.info("repository_removed", repo=repo_full_name)
        except Exception as exc:
            log.warning(
                "repository_remove_failed",
                repo=repo_full_name,
                error=str(exc),
            )

    return {
        "status": "completed",
        "action": "removed",
        "installation_id": installation_id,
        "repositories_removed": removed_repos,
        "count": len(removed_repos),
    }

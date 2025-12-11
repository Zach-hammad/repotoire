"""Webhook payload builders for different event types.

This module provides functions to build standardized webhook payloads
for each supported event type. All payloads follow a consistent structure
with event type, timestamp, and data fields.

Usage:
    from repotoire.services.webhook_payloads import build_analysis_completed_payload

    payload = build_analysis_completed_payload(
        analysis_run_id=run.id,
        repository_id=repo.id,
        repository_name=repo.full_name,
        commit_sha="abc123",
        health_score=85.5,
        finding_counts={"critical": 0, "high": 2, "medium": 5, "low": 10},
        duration_seconds=120,
    )
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID


def build_analysis_started_payload(
    analysis_run_id: UUID,
    repository_id: UUID,
    repository_name: str,
    commit_sha: str,
    triggered_by: str,
) -> dict[str, Any]:
    """Build payload for analysis.started event.

    Args:
        analysis_run_id: UUID of the analysis run.
        repository_id: UUID of the repository.
        repository_name: Full name of the repository (e.g., "owner/repo").
        commit_sha: Git commit SHA being analyzed.
        triggered_by: What triggered the analysis (e.g., "push", "pr", "manual").

    Returns:
        Webhook payload dictionary.
    """
    return {
        "event": "analysis.started",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "analysis_run_id": str(analysis_run_id),
            "repository_id": str(repository_id),
            "repository_name": repository_name,
            "commit_sha": commit_sha,
            "triggered_by": triggered_by,
        },
    }


def build_analysis_completed_payload(
    analysis_run_id: UUID,
    repository_id: UUID,
    repository_name: str,
    commit_sha: str,
    health_score: float,
    finding_counts: dict[str, int],
    duration_seconds: int,
    structure_score: float | None = None,
    quality_score: float | None = None,
    architecture_score: float | None = None,
) -> dict[str, Any]:
    """Build payload for analysis.completed event.

    Args:
        analysis_run_id: UUID of the analysis run.
        repository_id: UUID of the repository.
        repository_name: Full name of the repository.
        commit_sha: Git commit SHA that was analyzed.
        health_score: Overall health score (0-100).
        finding_counts: Dict mapping severity to count (e.g., {"critical": 2, "high": 5}).
        duration_seconds: How long the analysis took.
        structure_score: Optional structure score component.
        quality_score: Optional quality score component.
        architecture_score: Optional architecture score component.

    Returns:
        Webhook payload dictionary.
    """
    data = {
        "analysis_run_id": str(analysis_run_id),
        "repository_id": str(repository_id),
        "repository_name": repository_name,
        "commit_sha": commit_sha,
        "health_score": health_score,
        "findings": finding_counts,
        "duration_seconds": duration_seconds,
    }

    # Include component scores if available
    if structure_score is not None:
        data["structure_score"] = structure_score
    if quality_score is not None:
        data["quality_score"] = quality_score
    if architecture_score is not None:
        data["architecture_score"] = architecture_score

    return {
        "event": "analysis.completed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def build_analysis_failed_payload(
    analysis_run_id: UUID,
    repository_id: UUID,
    repository_name: str,
    commit_sha: str,
    error_message: str,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Build payload for analysis.failed event.

    Args:
        analysis_run_id: UUID of the analysis run.
        repository_id: UUID of the repository.
        repository_name: Full name of the repository.
        commit_sha: Git commit SHA that failed analysis.
        error_message: Human-readable error message.
        error_code: Optional error code for programmatic handling.

    Returns:
        Webhook payload dictionary.
    """
    data = {
        "analysis_run_id": str(analysis_run_id),
        "repository_id": str(repository_id),
        "repository_name": repository_name,
        "commit_sha": commit_sha,
        "error_message": error_message,
    }

    if error_code:
        data["error_code"] = error_code

    return {
        "event": "analysis.failed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def build_health_score_changed_payload(
    repository_id: UUID,
    repository_name: str,
    previous_score: float,
    current_score: float,
    change: float,
    analysis_run_id: UUID | None = None,
) -> dict[str, Any]:
    """Build payload for health_score.changed event.

    Triggered when health score changes significantly (e.g., > 5 points).

    Args:
        repository_id: UUID of the repository.
        repository_name: Full name of the repository.
        previous_score: Previous health score.
        current_score: New health score.
        change: Difference (current - previous).
        analysis_run_id: Optional analysis run that caused the change.

    Returns:
        Webhook payload dictionary.
    """
    data = {
        "repository_id": str(repository_id),
        "repository_name": repository_name,
        "previous_score": previous_score,
        "current_score": current_score,
        "change": change,
        "change_direction": "improved" if change > 0 else "degraded",
    }

    if analysis_run_id:
        data["analysis_run_id"] = str(analysis_run_id)

    return {
        "event": "health_score.changed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def build_finding_new_payload(
    repository_id: UUID,
    repository_name: str,
    finding_id: UUID,
    title: str,
    severity: str,
    detector: str,
    affected_files: list[str],
    analysis_run_id: UUID | None = None,
    description: str | None = None,
    suggested_fix: str | None = None,
) -> dict[str, Any]:
    """Build payload for finding.new event.

    Args:
        repository_id: UUID of the repository.
        repository_name: Full name of the repository.
        finding_id: UUID of the finding.
        title: Short title describing the finding.
        severity: Severity level (critical, high, medium, low, info).
        detector: Name of the detector that found the issue.
        affected_files: List of affected file paths.
        analysis_run_id: Optional analysis run that discovered the finding.
        description: Optional detailed description.
        suggested_fix: Optional fix suggestion.

    Returns:
        Webhook payload dictionary.
    """
    data = {
        "repository_id": str(repository_id),
        "repository_name": repository_name,
        "finding_id": str(finding_id),
        "title": title,
        "severity": severity,
        "detector": detector,
        "affected_files": affected_files,
    }

    if analysis_run_id:
        data["analysis_run_id"] = str(analysis_run_id)
    if description:
        data["description"] = description
    if suggested_fix:
        data["suggested_fix"] = suggested_fix

    return {
        "event": "finding.new",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def build_finding_resolved_payload(
    repository_id: UUID,
    repository_name: str,
    finding_id: UUID,
    title: str,
    severity: str,
    resolved_by: str | None = None,
    analysis_run_id: UUID | None = None,
) -> dict[str, Any]:
    """Build payload for finding.resolved event.

    Args:
        repository_id: UUID of the repository.
        repository_name: Full name of the repository.
        finding_id: UUID of the resolved finding.
        title: Short title of the finding.
        severity: Severity level of the resolved finding.
        resolved_by: How the finding was resolved (e.g., "fix", "ignore", "manual").
        analysis_run_id: Optional analysis run that detected resolution.

    Returns:
        Webhook payload dictionary.
    """
    data = {
        "repository_id": str(repository_id),
        "repository_name": repository_name,
        "finding_id": str(finding_id),
        "title": title,
        "severity": severity,
    }

    if resolved_by:
        data["resolved_by"] = resolved_by
    if analysis_run_id:
        data["analysis_run_id"] = str(analysis_run_id)

    return {
        "event": "finding.resolved",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def build_test_payload() -> dict[str, Any]:
    """Build a test payload for webhook endpoint verification.

    Returns:
        Test webhook payload dictionary.
    """
    return {
        "event": "webhook.test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "message": "This is a test webhook delivery from Repotoire.",
            "test": True,
        },
    }

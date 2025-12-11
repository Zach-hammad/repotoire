"""GitHub PR comment service for Repotoire.

This module provides functions to format and post analysis results
as comments on GitHub pull requests.

Features:
- Markdown formatting with health score and trend indicator
- Shows only NEW issues (not pre-existing)
- Groups issues by severity
- Updates existing comments (avoids duplicates)
- Links to full dashboard report
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
from sqlalchemy import select

from repotoire.db.models import AnalysisRun, AnalysisStatus
from repotoire.db.models.finding import Finding, FindingSeverity
from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = get_logger(__name__)

# Unique marker to identify Repotoire comments (for update/replace)
COMMENT_MARKER = "<!-- repotoire-analysis-comment -->"

# App base URL for dashboard links
APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://app.repotoire.io")


def format_pr_comment(
    analysis: AnalysisRun,
    new_findings: list[Finding],
    base_score: int | None,
    dashboard_url: str,
) -> str:
    """Format analysis results as PR comment markdown.

    Args:
        analysis: The completed AnalysisRun record.
        new_findings: List of findings that are NEW in this PR (not in base).
        base_score: Health score from the base branch analysis (for delta).
        dashboard_url: URL to the full analysis report.

    Returns:
        Markdown formatted comment body.
    """
    # Calculate score delta and trend indicator
    score = analysis.health_score or 0
    if base_score is not None:
        delta = score - base_score
        if delta > 0:
            trend = f"▲ +{delta}"
        elif delta < 0:
            trend = f"▼ {delta}"
        else:
            trend = "− no change"
        score_line = f"**{score}/100** ({trend} from base)"
    else:
        score_line = f"**{score}/100**"

    lines = [
        "## 🔍 Repotoire Analysis",
        "",
        f"### Health Score: {score_line}",
        "",
    ]

    # No new issues = success message
    if not new_findings:
        lines.extend([
            "✅ **No new issues found!**",
            "",
            "Great job! This PR doesn't introduce any new code quality issues.",
        ])
    else:
        lines.append(f"### New Issues Found ({len(new_findings)})")
        lines.append("")

        # Group findings by severity
        by_severity: dict[FindingSeverity, list[Finding]] = {}
        for finding in new_findings[:10]:  # Limit to 10 for readability
            severity = finding.severity
            if severity not in by_severity:
                by_severity[severity] = []
            by_severity[severity].append(finding)

        # Severity display config
        severity_icons = {
            FindingSeverity.CRITICAL: "🔴 Critical",
            FindingSeverity.HIGH: "🟠 High",
            FindingSeverity.MEDIUM: "🟡 Medium",
            FindingSeverity.LOW: "🟢 Low",
            FindingSeverity.INFO: "ℹ️ Info",
        }

        # Display order (most severe first)
        display_order = [
            FindingSeverity.CRITICAL,
            FindingSeverity.HIGH,
            FindingSeverity.MEDIUM,
            FindingSeverity.LOW,
        ]

        for severity in display_order:
            findings = by_severity.get(severity)
            if not findings:
                continue

            lines.append(f"#### {severity_icons.get(severity, severity.value)}")
            lines.append("| File | Issue | Line |")
            lines.append("|------|-------|------|")

            for finding in findings:
                # Get the first affected file for display
                file_path = finding.affected_files[0] if finding.affected_files else "unknown"
                # Shorten long file paths
                if len(file_path) > 40:
                    file_path = "..." + file_path[-37:]

                # Line number
                line_str = f"L{finding.line_start}" if finding.line_start else "—"

                # Truncate title if too long
                title = finding.title
                if len(title) > 60:
                    title = title[:57] + "..."

                lines.append(f"| `{file_path}` | {title} | {line_str} |")

            lines.append("")

        # Note if there are more issues
        if len(new_findings) > 10:
            lines.append(f"*...and {len(new_findings) - 10} more issues*")
            lines.append("")

    # Footer with link to dashboard
    lines.extend([
        "",
        "---",
        f"<sub>📊 [View full report]({dashboard_url}) · 🤖 Powered by [Repotoire](https://repotoire.io)</sub>",
        "",
        COMMENT_MARKER,
    ])

    return "\n".join(lines)


def get_new_findings(
    session: "Session",
    head_analysis_id: UUID,
    base_analysis_id: UUID | None,
) -> list[Finding]:
    """Get findings that are NEW in HEAD vs BASE.

    Compares findings between two analysis runs and returns only those
    that are new (not present in the base analysis). Uses a signature
    based on detector, title, and affected files.

    Args:
        session: SQLAlchemy session.
        head_analysis_id: UUID of the HEAD (PR) analysis run.
        base_analysis_id: UUID of the BASE (target branch) analysis run.
            If None, all HEAD findings are considered new.

    Returns:
        List of Finding objects that are new in HEAD.
    """
    # Get HEAD findings
    head_result = session.execute(
        select(Finding)
        .where(Finding.analysis_run_id == head_analysis_id)
        .order_by(Finding.severity.asc())  # Critical first
    )
    head_findings = list(head_result.scalars().all())

    if not base_analysis_id:
        # No base to compare against - all findings are "new"
        return head_findings

    # Get BASE findings for comparison
    base_result = session.execute(
        select(Finding).where(Finding.analysis_run_id == base_analysis_id)
    )
    base_findings = list(base_result.scalars().all())

    # Create signature set for base findings
    # Signature = (detector, title, first_affected_file)
    base_signatures = set()
    for f in base_findings:
        first_file = f.affected_files[0] if f.affected_files else ""
        signature = (f.detector, f.title, first_file)
        base_signatures.add(signature)

    # Return only findings not in base
    new_findings = []
    for f in head_findings:
        first_file = f.affected_files[0] if f.affected_files else ""
        signature = (f.detector, f.title, first_file)
        if signature not in base_signatures:
            new_findings.append(f)

    return new_findings


def get_base_analysis(
    session: "Session",
    repo_id: UUID,
    base_sha: str | None,
) -> AnalysisRun | None:
    """Get the most recent completed analysis for a commit.

    Args:
        session: SQLAlchemy session.
        repo_id: Repository UUID.
        base_sha: Git commit SHA to find analysis for.

    Returns:
        AnalysisRun if found, None otherwise.
    """
    if not base_sha:
        return None

    result = session.execute(
        select(AnalysisRun)
        .where(AnalysisRun.repository_id == repo_id)
        .where(AnalysisRun.commit_sha == base_sha)
        .where(AnalysisRun.status == AnalysisStatus.COMPLETED)
        .order_by(AnalysisRun.completed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def post_or_update_pr_comment(
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
    installation_token: str,
) -> dict:
    """Post or update a PR comment via GitHub API.

    Looks for an existing Repotoire comment (identified by COMMENT_MARKER)
    and updates it if found. Otherwise creates a new comment.

    Args:
        owner: Repository owner (user or org).
        repo: Repository name.
        pr_number: Pull request number.
        body: Comment body (markdown).
        installation_token: GitHub App installation access token.

    Returns:
        dict with 'comment_id', 'action' ('created' or 'updated'), and 'url'.

    Raises:
        httpx.HTTPStatusError: If the API request fails.
    """
    headers = {
        "Authorization": f"Bearer {installation_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    comments_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_number}/comments"

    with httpx.Client(timeout=30.0) as client:
        # Check for existing Repotoire comment
        existing_comment_id = None
        try:
            resp = client.get(comments_url, headers=headers)
            resp.raise_for_status()

            for comment in resp.json():
                comment_body = comment.get("body", "")
                if COMMENT_MARKER in comment_body:
                    existing_comment_id = comment["id"]
                    break
        except httpx.HTTPStatusError as e:
            logger.warning(f"Failed to fetch existing comments: {e}")
            # Continue to try creating a new comment

        if existing_comment_id:
            # Update existing comment
            update_url = f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{existing_comment_id}"
            resp = client.patch(update_url, headers=headers, json={"body": body})
            resp.raise_for_status()

            result = resp.json()
            logger.info(f"Updated PR comment {existing_comment_id} on {owner}/{repo}#{pr_number}")
            return {
                "comment_id": str(result["id"]),
                "action": "updated",
                "url": result.get("html_url"),
            }
        else:
            # Create new comment
            resp = client.post(comments_url, headers=headers, json={"body": body})
            resp.raise_for_status()

            result = resp.json()
            logger.info(f"Created PR comment on {owner}/{repo}#{pr_number}")
            return {
                "comment_id": str(result["id"]),
                "action": "created",
                "url": result.get("html_url"),
            }


def get_installation_token_for_repo(repo_id: UUID) -> str | None:
    """Get GitHub installation token for a repository.

    Looks up the repository's installation ID and retrieves or refreshes
    the installation access token.

    Args:
        repo_id: Repository UUID.

    Returns:
        Installation access token or None if unavailable.
    """
    import asyncio

    from repotoire.api.services.encryption import TokenEncryption
    from repotoire.api.services.github import GitHubAppClient
    from repotoire.db.models import GitHubInstallation, Repository
    from repotoire.db.session import get_sync_session

    try:
        with get_sync_session() as session:
            repo = session.get(Repository, repo_id)
            if not repo or not repo.github_installation_id:
                logger.warning(f"No installation ID for repo {repo_id}")
                return os.environ.get("GITHUB_TOKEN")

            # Find the GitHubInstallation record
            result = session.execute(
                select(GitHubInstallation).where(
                    GitHubInstallation.installation_id == repo.github_installation_id
                )
            )
            installation = result.scalar_one_or_none()

            if not installation:
                logger.warning(f"GitHubInstallation not found for installation_id={repo.github_installation_id}")
                return os.environ.get("GITHUB_TOKEN")

            encryption = TokenEncryption()
            github_client = GitHubAppClient()

            # Refresh token if expiring soon
            if github_client.is_token_expiring_soon(installation.token_expires_at):
                logger.info(f"Refreshing expired token for installation {installation.installation_id}")
                new_token, expires_at = asyncio.run(
                    github_client.get_installation_token(installation.installation_id)
                )
                installation.access_token_encrypted = encryption.encrypt(new_token)
                installation.token_expires_at = expires_at
                session.commit()
                return new_token

            return encryption.decrypt(installation.access_token_encrypted)

    except Exception as e:
        logger.error(f"Failed to get installation token: {e}")
        return os.environ.get("GITHUB_TOKEN")

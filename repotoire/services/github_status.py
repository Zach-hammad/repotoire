"""GitHub commit status API service.

This module provides functions to set commit statuses via the GitHub API,
enabling integration with GitHub's required status checks feature.

Status checks appear on pull requests and can block merging when
configured as required checks in branch protection rules.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING

import httpx

from repotoire.logging_config import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# App base URL for status target links
APP_BASE_URL = os.environ.get("APP_BASE_URL", "https://app.repotoire.io")


class CommitState(str, Enum):
    """GitHub commit status states."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"


def set_commit_status(
    installation_token: str,
    repo_full_name: str,
    sha: str,
    state: CommitState,
    description: str,
    target_url: str | None = None,
    context: str = "repotoire/analysis",
) -> dict | None:
    """Set commit status via GitHub API (sync version).

    Creates a commit status check that appears on PRs and commits in GitHub.
    These can be configured as required checks in branch protection rules.

    Args:
        installation_token: GitHub App installation access token.
        repo_full_name: Full repository name (owner/repo).
        sha: Git commit SHA to set status on.
        state: Status state (pending, success, failure, error).
        description: Short description (max 140 chars, will be truncated).
        target_url: URL to link to (e.g., analysis dashboard).
        context: Status context name (default: "repotoire/analysis").

    Returns:
        GitHub API response dict or None if request failed.
    """
    url = f"https://api.github.com/repos/{repo_full_name}/statuses/{sha}"

    # Truncate description to GitHub's limit
    description = description[:140]

    payload = {
        "state": state.value,
        "description": description,
        "context": context,
    }

    if target_url:
        payload["target_url"] = target_url

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {installation_token}",
                    "Accept": "application/vnd.github.v3+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=payload,
            )
            response.raise_for_status()

            logger.info(
                "commit_status_set",
                repo=repo_full_name,
                sha=sha[:8],
                state=state.value,
                context=context,
            )
            return response.json()

    except httpx.HTTPStatusError as e:
        logger.error(
            "commit_status_failed",
            repo=repo_full_name,
            sha=sha[:8],
            state=state.value,
            status_code=e.response.status_code,
            error=str(e),
        )
        return None
    except Exception as e:
        logger.error(
            "commit_status_error",
            repo=repo_full_name,
            sha=sha[:8],
            error=str(e),
        )
        return None


async def set_commit_status_async(
    installation_token: str,
    repo_full_name: str,
    sha: str,
    state: CommitState,
    description: str,
    target_url: str | None = None,
    context: str = "repotoire/analysis",
) -> dict | None:
    """Set commit status via GitHub API (async version).

    Creates a commit status check that appears on PRs and commits in GitHub.
    These can be configured as required checks in branch protection rules.

    Args:
        installation_token: GitHub App installation access token.
        repo_full_name: Full repository name (owner/repo).
        sha: Git commit SHA to set status on.
        state: Status state (pending, success, failure, error).
        description: Short description (max 140 chars, will be truncated).
        target_url: URL to link to (e.g., analysis dashboard).
        context: Status context name (default: "repotoire/analysis").

    Returns:
        GitHub API response dict or None if request failed.
    """
    url = f"https://api.github.com/repos/{repo_full_name}/statuses/{sha}"

    # Truncate description to GitHub's limit
    description = description[:140]

    payload = {
        "state": state.value,
        "description": description,
        "context": context,
    }

    if target_url:
        payload["target_url"] = target_url

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {installation_token}",
                    "Accept": "application/vnd.github.v3+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=payload,
            )
            response.raise_for_status()

            logger.info(
                "commit_status_set",
                repo=repo_full_name,
                sha=sha[:8],
                state=state.value,
                context=context,
            )
            return response.json()

    except httpx.HTTPStatusError as e:
        logger.error(
            "commit_status_failed",
            repo=repo_full_name,
            sha=sha[:8],
            state=state.value,
            status_code=e.response.status_code,
            error=str(e),
        )
        return None
    except Exception as e:
        logger.error(
            "commit_status_error",
            repo=repo_full_name,
            sha=sha[:8],
            error=str(e),
        )
        return None


def build_analysis_url(analysis_run_id: str, repo_id: str | None = None) -> str:
    """Build URL to analysis dashboard for status target_url.

    Args:
        analysis_run_id: UUID of the analysis run.
        repo_id: Optional repository UUID for better URL.

    Returns:
        URL to the analysis dashboard.
    """
    if repo_id:
        return f"{APP_BASE_URL}/repos/{repo_id}/analysis/{analysis_run_id}"
    return f"{APP_BASE_URL}/analysis/{analysis_run_id}"

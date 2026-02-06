"""GitHub Git API service for fetching repository history.

This module provides functions to fetch git history data via the GitHub API,
enabling team analytics features like code ownership analysis.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx

from repotoire.db.models import GitHubInstallation, GitHubRepository
from repotoire.logging_config import get_logger
from repotoire.utils.encryption import decrypt_api_key

logger = get_logger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GitHubGitService:
    """Service for fetching git data from GitHub repositories."""
    
    def __init__(self, installation: GitHubInstallation):
        """Initialize with a GitHub installation.
        
        Args:
            installation: GitHub App installation with access token
        """
        self.installation = installation
        self._token: Optional[str] = None
    
    def _get_token(self) -> str:
        """Get decrypted access token."""
        if self._token is None:
            self._token = decrypt_api_key(self.installation.access_token_encrypted)
        return self._token
    
    def _headers(self) -> Dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"token {self._get_token()}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    
    async def fetch_commits(
        self,
        repo_full_name: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        per_page: int = 100,
        max_commits: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Fetch commit history for a repository.
        
        Args:
            repo_full_name: Full repository name (owner/repo)
            since: Only commits after this date
            until: Only commits before this date
            per_page: Results per page (max 100)
            max_commits: Maximum total commits to fetch
            
        Returns:
            List of commit objects with author info
        """
        commits = []
        page = 1
        
        # Default to last 90 days if no since date
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(days=90)
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            while len(commits) < max_commits:
                params = {
                    "per_page": per_page,
                    "page": page,
                }
                if since:
                    params["since"] = since.isoformat()
                if until:
                    params["until"] = until.isoformat()
                
                try:
                    response = await client.get(
                        f"{GITHUB_API_BASE}/repos/{repo_full_name}/commits",
                        headers=self._headers(),
                        params=params,
                    )
                    response.raise_for_status()
                    page_commits = response.json()
                    
                    if not page_commits:
                        break
                    
                    commits.extend(page_commits)
                    page += 1
                    
                    # Rate limit courtesy delay
                    await asyncio.sleep(0.1)
                    
                except httpx.HTTPStatusError as e:
                    logger.error(f"GitHub API error fetching commits: {e}")
                    break
                except Exception as e:
                    logger.error(f"Error fetching commits: {e}")
                    break
        
        logger.info(f"Fetched {len(commits)} commits from {repo_full_name}")
        return commits[:max_commits]
    
    async def fetch_commit_details(
        self,
        repo_full_name: str,
        sha: str,
    ) -> Optional[Dict[str, Any]]:
        """Fetch detailed commit info including file changes.
        
        Args:
            repo_full_name: Full repository name
            sha: Commit SHA
            
        Returns:
            Commit details with files array
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    f"{GITHUB_API_BASE}/repos/{repo_full_name}/commits/{sha}",
                    headers=self._headers(),
                )
                response.raise_for_status()
                return response.json()
            except Exception as e:
                logger.error(f"Error fetching commit {sha}: {e}")
                return None
    
    async def fetch_git_log(
        self,
        repo_full_name: str,
        since: Optional[datetime] = None,
        max_commits: int = 500,
    ) -> List[Dict[str, Any]]:
        """Fetch git log with file changes for ownership analysis.
        
        This fetches commits and their file changes in a format suitable
        for the TeamAnalyticsService.analyze_git_ownership method.
        
        Args:
            repo_full_name: Full repository name
            since: Only commits after this date
            max_commits: Maximum commits to process
            
        Returns:
            List of commit dicts with author, timestamp, and files
        """
        # First get commit list
        commits = await self.fetch_commits(
            repo_full_name,
            since=since,
            max_commits=max_commits,
        )
        
        git_log = []
        
        # Fetch details for each commit (with rate limiting)
        for i, commit in enumerate(commits):
            sha = commit.get("sha")
            if not sha:
                continue
            
            # Get commit details with files
            details = await self.fetch_commit_details(repo_full_name, sha)
            if not details:
                continue
            
            # Extract author info
            commit_data = details.get("commit", {})
            author = commit_data.get("author", {})
            
            # Extract file changes
            files = []
            for file_info in details.get("files", []):
                files.append({
                    "path": file_info.get("filename", ""),
                    "lines_added": file_info.get("additions", 0),
                    "lines_removed": file_info.get("deletions", 0),
                    "status": file_info.get("status", "modified"),
                })
            
            # Parse timestamp
            timestamp = None
            date_str = author.get("date")
            if date_str:
                try:
                    timestamp = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except Exception:
                    pass
            
            git_log.append({
                "sha": sha,
                "author_name": author.get("name", "Unknown"),
                "author_email": author.get("email", ""),
                "timestamp": timestamp,
                "message": commit_data.get("message", ""),
                "files": files,
            })
            
            # Rate limiting - be gentle with GitHub API
            if (i + 1) % 10 == 0:
                await asyncio.sleep(0.5)
            else:
                await asyncio.sleep(0.1)
        
        logger.info(f"Built git log with {len(git_log)} commits and file details")
        return git_log


async def get_git_service_for_repo(
    session,
    repository_id: UUID,
) -> Optional[GitHubGitService]:
    """Get a GitHubGitService for a repository.
    
    Args:
        session: Database session
        repository_id: Repository UUID
        
    Returns:
        GitHubGitService if repository has GitHub installation, None otherwise
    """
    from sqlalchemy import select
    from repotoire.db.models import Repository
    
    # Get repository with GitHub info
    result = await session.execute(
        select(GitHubRepository).where(GitHubRepository.repository_id == repository_id)
    )
    github_repo = result.scalar_one_or_none()
    
    if not github_repo:
        logger.warning(f"No GitHub repository found for {repository_id}")
        return None
    
    # Get installation
    result = await session.execute(
        select(GitHubInstallation).where(
            GitHubInstallation.id == github_repo.installation_id
        )
    )
    installation = result.scalar_one_or_none()
    
    if not installation:
        logger.warning(f"No GitHub installation found for repo {repository_id}")
        return None
    
    return GitHubGitService(installation)

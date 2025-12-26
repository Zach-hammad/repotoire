"""Repository detection utilities for CLI (REPO-397).

Provides utilities to detect repository information from local directories,
including git remote URLs and deriving deterministic repo IDs.
"""

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import UUID

from repotoire.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class RepoInfo:
    """Information about a repository."""

    # Deterministic repo ID (UUID derived from remote URL or path)
    repo_id: str

    # Human-readable slug (e.g., "owner/repo" or directory name)
    repo_slug: str

    # Git remote URL if available
    remote_url: Optional[str] = None

    # Default branch if detected
    default_branch: Optional[str] = None

    # Source of the repo info
    source: str = "local"  # "git" or "local"


def get_git_remote_url(repo_path: Path) -> Optional[str]:
    """Get the git remote URL for a repository.

    Args:
        repo_path: Path to the repository

    Returns:
        Remote URL if found, None otherwise
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug(f"Failed to get git remote URL: {e}")
    return None


def get_git_default_branch(repo_path: Path) -> Optional[str]:
    """Get the default branch for a repository.

    Args:
        repo_path: Path to the repository

    Returns:
        Default branch name if found, None otherwise
    """
    try:
        # Try to get the branch that HEAD points to on origin
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug(f"Failed to get git default branch: {e}")
    return None


def normalize_remote_url(url: str) -> str:
    """Normalize a git remote URL for consistent hashing.

    Handles:
    - SSH URLs: git@github.com:owner/repo.git -> github.com/owner/repo
    - HTTPS URLs: https://github.com/owner/repo.git -> github.com/owner/repo
    - With or without .git suffix

    Args:
        url: Git remote URL

    Returns:
        Normalized URL string
    """
    # Remove .git suffix
    url = re.sub(r"\.git$", "", url)

    # Handle SSH URLs (git@github.com:owner/repo)
    ssh_match = re.match(r"git@([^:]+):(.+)", url)
    if ssh_match:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}"

    # Handle HTTPS URLs (https://github.com/owner/repo)
    https_match = re.match(r"https?://([^/]+)/(.+)", url)
    if https_match:
        return f"{https_match.group(1)}/{https_match.group(2)}"

    # Return as-is if no pattern matches
    return url


def extract_repo_slug(url: str) -> str:
    """Extract owner/repo slug from a git URL.

    Args:
        url: Git remote URL or normalized URL

    Returns:
        Repo slug (e.g., "owner/repo")
    """
    normalized = normalize_remote_url(url)

    # Extract the last two path components (owner/repo)
    parts = normalized.split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return parts[-1] if parts else "unknown"


def derive_repo_id(identifier: str) -> str:
    """Derive a deterministic UUID-like repo ID from an identifier.

    Creates a consistent ID that can be used to match repos across ingestions.
    Uses SHA-256 hash truncated to UUID format.

    Args:
        identifier: Unique identifier (e.g., normalized remote URL or absolute path)

    Returns:
        UUID-formatted string (e.g., "550e8400-e29b-41d4-a716-446655440000")
    """
    # Hash the identifier
    hash_bytes = hashlib.sha256(identifier.encode("utf-8")).digest()

    # Format as UUID (use first 16 bytes)
    hex_str = hash_bytes[:16].hex()
    return f"{hex_str[:8]}-{hex_str[8:12]}-{hex_str[12:16]}-{hex_str[16:20]}-{hex_str[20:32]}"


def detect_repo_info(repo_path: Path) -> RepoInfo:
    """Detect repository information from a local path.

    Attempts to extract git remote URL and derive repo ID.
    Falls back to using the directory path if not a git repo.

    Args:
        repo_path: Path to the repository

    Returns:
        RepoInfo with detected information
    """
    repo_path = repo_path.resolve()

    # Try to get git remote URL
    remote_url = get_git_remote_url(repo_path)

    if remote_url:
        # Git repository with remote
        normalized = normalize_remote_url(remote_url)
        repo_id = derive_repo_id(normalized)
        repo_slug = extract_repo_slug(remote_url)
        default_branch = get_git_default_branch(repo_path)

        logger.debug(f"Detected git repo: {repo_slug} (id: {repo_id[:8]}...)")

        return RepoInfo(
            repo_id=repo_id,
            repo_slug=repo_slug,
            remote_url=remote_url,
            default_branch=default_branch,
            source="git",
        )
    else:
        # Local directory (not a git repo or no remote)
        # Use absolute path as identifier
        path_str = str(repo_path)
        repo_id = derive_repo_id(path_str)
        repo_slug = repo_path.name  # Just the directory name

        logger.debug(f"Using local path as repo identifier: {repo_slug}")

        return RepoInfo(
            repo_id=repo_id,
            repo_slug=repo_slug,
            source="local",
        )

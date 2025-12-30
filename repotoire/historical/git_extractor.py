"""Git commit extraction for cloud-only architecture.

This module extracts git commit data from local repositories for sending
to the cloud API. Unlike git_graphiti.py which does Graphiti processing
client-side, this module only extracts raw commit metadata.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from repotoire.logging_config import get_logger

logger = get_logger(__name__)

# GitPython is a core dependency (already in requirements for git_graphiti.py)
try:
    import git
    GIT_AVAILABLE = True
except ImportError:
    GIT_AVAILABLE = False
    git = None  # type: ignore


def is_git_repository(repo_path: str | Path) -> bool:
    """Check if a path is a git repository.

    Args:
        repo_path: Path to check

    Returns:
        True if the path is a git repository
    """
    if not GIT_AVAILABLE:
        return False

    try:
        git.Repo(repo_path)
        return True
    except (git.exc.InvalidGitRepositoryError, git.exc.NoSuchPathError):
        return False


def extract_commits(
    repo_path: str | Path,
    max_commits: int = 100,
    since: Optional[datetime] = None,
    branch: str = "main",
) -> List[Dict[str, Any]]:
    """Extract commit data from a git repository.

    Args:
        repo_path: Path to git repository
        max_commits: Maximum number of commits to extract
        since: Only extract commits after this date
        branch: Git branch to analyze

    Returns:
        List of commit data dictionaries ready for API ingestion

    Raises:
        ImportError: If gitpython is not installed
        git.exc.InvalidGitRepositoryError: If path is not a git repository
    """
    if not GIT_AVAILABLE:
        raise ImportError(
            "GitPython is required for git history extraction. "
            "Install with: pip install gitpython"
        )

    repo = git.Repo(repo_path)
    commits_data = []

    try:
        # Try the specified branch, fall back to current HEAD
        try:
            commit_iter = repo.iter_commits(branch, max_count=max_commits)
        except git.exc.GitCommandError:
            # Branch doesn't exist, try HEAD
            logger.warning(f"Branch '{branch}' not found, using HEAD")
            commit_iter = repo.iter_commits("HEAD", max_count=max_commits)

        commits = list(commit_iter)
    except Exception as e:
        logger.error(f"Failed to get commits: {e}")
        return []

    # Filter by date if specified
    if since:
        commits = [c for c in commits if c.committed_datetime >= since]

    for commit in commits:
        try:
            commit_data = _extract_commit_data(commit)
            commits_data.append(commit_data)
        except Exception as e:
            logger.warning(f"Failed to extract commit {commit.hexsha[:8]}: {e}")
            continue

    logger.info(f"Extracted {len(commits_data)} commits from {repo_path}")
    return commits_data


def _extract_commit_data(commit: "git.Commit") -> Dict[str, Any]:
    """Extract data from a single commit.

    Args:
        commit: GitPython commit object

    Returns:
        Dictionary with commit data for API ingestion
    """
    # Get changed files and stats
    if commit.parents:
        parent = commit.parents[0]
        diffs = parent.diff(commit)
        changed_files = [d.a_path or d.b_path for d in diffs if d.a_path or d.b_path]
        code_changes = _extract_code_changes(diffs)
    else:
        # Initial commit
        changed_files = list(commit.stats.files.keys())
        code_changes = []

    return {
        "sha": commit.hexsha,
        "author_name": commit.author.name,
        "author_email": commit.author.email,
        "committed_date": commit.committed_datetime.isoformat(),
        "message": commit.message,
        "changed_files": changed_files[:50],  # Limit to avoid huge payloads
        "insertions": commit.stats.total.get("insertions", 0),
        "deletions": commit.stats.total.get("deletions", 0),
        "code_changes": code_changes[:20],  # Limit detected changes
    }


def _extract_code_changes(diffs: "git.DiffIndex") -> List[str]:
    """Extract function/class changes from diff objects.

    Args:
        diffs: GitPython diff index

    Returns:
        List of code change descriptions
    """
    changes = []

    for diff in diffs:
        file_path = diff.a_path or diff.b_path
        if not file_path:
            continue

        # Only process Python files for now
        if not file_path.endswith(".py"):
            continue

        if not diff.diff:
            continue

        try:
            diff_text = diff.diff.decode("utf-8", errors="ignore")

            # Extract added/modified functions
            func_pattern = r"^\+\s*(?:async\s+)?def\s+(\w+)"
            funcs = re.findall(func_pattern, diff_text, re.MULTILINE)

            # Extract added/modified classes
            class_pattern = r"^\+\s*class\s+(\w+)"
            classes = re.findall(class_pattern, diff_text, re.MULTILINE)

            for func in funcs:
                changes.append(f"Modified function: {func} in {file_path}")

            for cls in classes:
                changes.append(f"Modified class: {cls} in {file_path}")

        except Exception:
            continue

    return changes

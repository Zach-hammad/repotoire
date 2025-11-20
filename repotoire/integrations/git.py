"""Git repository integration for temporal code analysis."""

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set
import os

from repotoire.models import GitCommit
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class GitRepository:
    """Extract commit history and file changes from Git repository.

    Provides access to Git commit history, file changes, and file contents
    at specific commits for temporal code analysis.

    Example:
        >>> repo = GitRepository("/path/to/repo")
        >>> commits = repo.get_commit_history(max_commits=10)
        >>> for commit in commits:
        ...     print(f"{commit.short_hash}: {commit.message}")
        >>> content = repo.get_file_at_commit("src/file.py", commit.hash)
    """

    def __init__(self, repo_path: str):
        """Initialize Git repository wrapper.

        Args:
            repo_path: Path to the Git repository

        Raises:
            ImportError: If GitPython is not installed
            ValueError: If path is not a Git repository
        """
        try:
            import git
        except ImportError:
            raise ImportError(
                "GitPython is required for temporal tracking. "
                "Install with: pip install gitpython"
            )

        self.repo_path = Path(repo_path).resolve()

        try:
            self.repo = git.Repo(self.repo_path)
        except git.exc.InvalidGitRepositoryError:
            raise ValueError(f"Not a Git repository: {repo_path}")

        logger.info(f"Initialized Git repository: {self.repo_path}")

    def get_commit_history(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        branch: str = "HEAD",
        max_commits: int = 100,
        skip_merges: bool = False,
    ) -> List[GitCommit]:
        """Get commit history with file changes.

        Args:
            since: Optional start datetime (commits after this time)
            until: Optional end datetime (commits before this time)
            branch: Branch or commit to start from (default: HEAD)
            max_commits: Maximum number of commits to retrieve
            skip_merges: Whether to skip merge commits

        Returns:
            List of GitCommit objects ordered newest to oldest

        Example:
            >>> repo = GitRepository(".")
            >>> commits = repo.get_commit_history(max_commits=5)
            >>> len(commits) <= 5
            True
        """
        commits = []

        try:
            # Build kwargs for iter_commits
            kwargs = {"max_count": max_commits}

            if since:
                kwargs["since"] = since
            if until:
                kwargs["until"] = until
            if skip_merges:
                kwargs["no_merges"] = True

            for commit in self.repo.iter_commits(branch, **kwargs):
                # Get changed files
                changed_files = self._get_changed_files(commit)

                # Get commit stats
                stats = commit.stats.total

                commits.append(GitCommit(
                    hash=commit.hexsha,
                    short_hash=commit.hexsha[:7],
                    message=commit.message.strip(),
                    author=commit.author.name,
                    author_email=commit.author.email,
                    committed_at=datetime.fromtimestamp(commit.committed_date),
                    parent_hashes=[p.hexsha for p in commit.parents],
                    branch=self._get_branch_for_commit(commit),
                    changed_files=changed_files,
                    stats={
                        "insertions": stats.get("insertions", 0),
                        "deletions": stats.get("deletions", 0),
                        "files_changed": stats.get("files", 0),
                    }
                ))

            logger.debug(f"Retrieved {len(commits)} commits from {branch}")
            return commits

        except Exception as e:
            logger.error(f"Failed to get commit history: {e}")
            return []

    def get_tagged_commits(self) -> List[GitCommit]:
        """Get commits that have tags (releases/milestones).

        Returns:
            List of GitCommit objects for tagged commits

        Example:
            >>> repo = GitRepository(".")
            >>> tagged = repo.get_tagged_commits()
            >>> all(commit.hash in [tag.commit.hexsha for tag in repo.repo.tags] for commit in tagged)
            True
        """
        tagged_commits = []

        for tag in self.repo.tags:
            commit = tag.commit
            changed_files = self._get_changed_files(commit)
            stats = commit.stats.total

            tagged_commits.append(GitCommit(
                hash=commit.hexsha,
                short_hash=commit.hexsha[:7],
                message=f"[{tag.name}] {commit.message.strip()}",
                author=commit.author.name,
                author_email=commit.author.email,
                committed_at=datetime.fromtimestamp(commit.committed_date),
                parent_hashes=[p.hexsha for p in commit.parents],
                branch=self._get_branch_for_commit(commit),
                changed_files=changed_files,
                stats={
                    "insertions": stats.get("insertions", 0),
                    "deletions": stats.get("deletions", 0),
                    "files_changed": stats.get("files", 0),
                }
            ))

        # Sort by commit date, newest first
        tagged_commits.sort(key=lambda c: c.committed_at, reverse=True)

        logger.debug(f"Retrieved {len(tagged_commits)} tagged commits")
        return tagged_commits

    def get_file_at_commit(self, file_path: str, commit_hash: str) -> Optional[str]:
        """Get file contents at a specific commit.

        Args:
            file_path: Relative path to file from repository root
            commit_hash: Git commit hash

        Returns:
            File contents as string, or None if file didn't exist at that commit

        Example:
            >>> repo = GitRepository(".")
            >>> content = repo.get_file_at_commit("README.md", "abc123")
            >>> content is None or isinstance(content, str)
            True
        """
        try:
            commit = self.repo.commit(commit_hash)

            # Normalize path separators
            file_path = file_path.replace(os.sep, '/')

            # Get file from commit tree
            try:
                blob = commit.tree / file_path
                return blob.data_stream.read().decode('utf-8', errors='replace')
            except KeyError:
                # File didn't exist at this commit
                logger.debug(f"File {file_path} not found at commit {commit_hash[:7]}")
                return None

        except Exception as e:
            logger.warning(f"Failed to get file {file_path} at commit {commit_hash[:7]}: {e}")
            return None

    def get_current_branch(self) -> str:
        """Get current active branch name.

        Returns:
            Branch name (e.g., "main", "develop")

        Example:
            >>> repo = GitRepository(".")
            >>> branch = repo.get_current_branch()
            >>> isinstance(branch, str)
            True
        """
        try:
            return self.repo.active_branch.name
        except TypeError:
            # Detached HEAD state
            return "HEAD"

    def get_all_branches(self) -> List[str]:
        """Get list of all branch names.

        Returns:
            List of branch names

        Example:
            >>> repo = GitRepository(".")
            >>> branches = repo.get_all_branches()
            >>> isinstance(branches, list)
            True
        """
        return [branch.name for branch in self.repo.branches]

    def is_dirty(self) -> bool:
        """Check if repository has uncommitted changes.

        Returns:
            True if there are uncommitted changes

        Example:
            >>> repo = GitRepository(".")
            >>> isinstance(repo.is_dirty(), bool)
            True
        """
        return self.repo.is_dirty()

    def get_latest_commit(self, branch: str = "HEAD") -> Optional[GitCommit]:
        """Get the most recent commit on a branch.

        Args:
            branch: Branch name (default: HEAD)

        Returns:
            GitCommit object or None if branch doesn't exist

        Example:
            >>> repo = GitRepository(".")
            >>> latest = repo.get_latest_commit()
            >>> latest is not None
            True
        """
        commits = self.get_commit_history(branch=branch, max_commits=1)
        return commits[0] if commits else None

    def _get_changed_files(self, commit) -> List[str]:
        """Get list of files changed in a commit.

        Args:
            commit: GitPython commit object

        Returns:
            List of file paths changed in the commit
        """
        changed_files = []

        try:
            # For commits with parents, get diff
            if commit.parents:
                parent = commit.parents[0]
                diffs = parent.diff(commit)

                for diff in diffs:
                    # Handle both added and modified files
                    if diff.b_path:
                        changed_files.append(diff.b_path)
                    elif diff.a_path:
                        # Deleted file
                        changed_files.append(diff.a_path)
            else:
                # Initial commit - all files are new
                for item in commit.tree.traverse():
                    if item.type == 'blob':  # It's a file
                        changed_files.append(item.path)

        except Exception as e:
            logger.debug(f"Could not get changed files for commit {commit.hexsha[:7]}: {e}")

        return changed_files

    def _get_branch_for_commit(self, commit) -> str:
        """Get branch name containing this commit.

        Args:
            commit: GitPython commit object

        Returns:
            Branch name or "detached" if not on a branch
        """
        try:
            # Check if commit is on current branch
            if commit in self.repo.iter_commits(self.repo.active_branch):
                return self.repo.active_branch.name
        except (TypeError, AttributeError):
            pass

        # Try to find branch containing this commit
        for branch in self.repo.branches:
            if commit in self.repo.iter_commits(branch):
                return branch.name

        return "detached"

    def get_file_history(
        self,
        file_path: str,
        max_commits: int = 50
    ) -> List[GitCommit]:
        """Get commit history for a specific file.

        Args:
            file_path: Path to file relative to repository root
            max_commits: Maximum number of commits to retrieve

        Returns:
            List of commits that modified this file

        Example:
            >>> repo = GitRepository(".")
            >>> history = repo.get_file_history("README.md", max_commits=10)
            >>> len(history) <= 10
            True
        """
        commits = []

        try:
            # Normalize path
            file_path = file_path.replace(os.sep, '/')

            for commit in self.repo.iter_commits(paths=file_path, max_count=max_commits):
                changed_files = [file_path]  # We know this file was changed
                stats = commit.stats.total

                commits.append(GitCommit(
                    hash=commit.hexsha,
                    short_hash=commit.hexsha[:7],
                    message=commit.message.strip(),
                    author=commit.author.name,
                    author_email=commit.author.email,
                    committed_at=datetime.fromtimestamp(commit.committed_date),
                    parent_hashes=[p.hexsha for p in commit.parents],
                    branch=self._get_branch_for_commit(commit),
                    changed_files=changed_files,
                    stats={
                        "insertions": stats.get("insertions", 0),
                        "deletions": stats.get("deletions", 0),
                        "files_changed": 1,
                    }
                ))

            logger.debug(f"Retrieved {len(commits)} commits for file {file_path}")
            return commits

        except Exception as e:
            logger.error(f"Failed to get file history for {file_path}: {e}")
            return []

    def get_authors(self) -> Set[str]:
        """Get all unique commit authors.

        Returns:
            Set of author names

        Example:
            >>> repo = GitRepository(".")
            >>> authors = repo.get_authors()
            >>> isinstance(authors, set)
            True
        """
        authors = set()

        for commit in self.repo.iter_commits():
            authors.add(commit.author.name)

        return authors

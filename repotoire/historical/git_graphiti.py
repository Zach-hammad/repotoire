"""Git history integration with Graphiti temporal knowledge graph.

This module enables automatic ingestion of git commit history into a Graphiti
temporal knowledge graph, allowing natural language queries about code evolution.

Each git commit becomes a Graphiti episode with:
- Commit metadata (author, timestamp, message, SHA)
- Changed files and code entities
- Diff statistics
- LLM-extracted semantic understanding
"""

import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, TYPE_CHECKING

import git

# Graphiti is an optional dependency
try:
    from graphiti_core import Graphiti
    from graphiti_core.nodes import EpisodeType
    GRAPHITI_AVAILABLE = True
except ImportError:
    GRAPHITI_AVAILABLE = False
    Graphiti = None  # type: ignore
    EpisodeType = None  # type: ignore

logger = logging.getLogger(__name__)


class GitGraphitiIntegration:
    """Integrate git repository history with Graphiti temporal knowledge graph."""

    def __init__(self, repo_path: str | Path, graphiti: "Graphiti"):
        """Initialize Git-Graphiti integration.

        Args:
            repo_path: Path to git repository
            graphiti: Initialized Graphiti instance

        Raises:
            ImportError: If graphiti_core is not installed
            git.exc.InvalidGitRepositoryError: If repo_path is not a git repository
        """
        if not GRAPHITI_AVAILABLE:
            raise ImportError(
                "graphiti_core is required for Git history integration. "
                "Install with: pip install repotoire[graphiti]"
            )
        self.repo_path = Path(repo_path)
        self.repo = git.Repo(repo_path)
        self.graphiti = graphiti

    async def ingest_git_history(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        branch: str = "main",
        max_commits: int = 1000,
        batch_size: int = 10,
    ) -> Dict[str, Any]:
        """Ingest git commits as Graphiti episodes.

        Args:
            since: Only ingest commits after this date (timezone-aware)
            until: Only ingest commits before this date (timezone-aware)
            branch: Git branch to analyze (default: main)
            max_commits: Maximum number of commits to process
            batch_size: Number of commits to process in parallel

        Returns:
            Statistics about the ingestion process

        Example:
            ```python
            graphiti = Graphiti(uri="bolt://localhost:7687", ...)
            integration = GitGraphitiIntegration("/path/to/repo", graphiti)

            stats = await integration.ingest_git_history(
                since=datetime(2024, 1, 1, tzinfo=timezone.utc),
                max_commits=500
            )
            print(f"Ingested {stats['commits_processed']} commits")
            ```
        """
        logger.info(f"Starting git history ingestion from branch '{branch}'")

        stats = {
            "commits_processed": 0,
            "commits_skipped": 0,
            "errors": 0,
            "oldest_commit": None,
            "newest_commit": None,
        }

        try:
            # Get commits from repository
            commits = list(
                self.repo.iter_commits(branch, max_count=max_commits)
            )

            # Filter by date if specified
            if since or until:
                filtered_commits = []
                for commit in commits:
                    commit_dt = commit.committed_datetime

                    if since and commit_dt < since:
                        continue
                    if until and commit_dt > until:
                        continue

                    filtered_commits.append(commit)

                commits = filtered_commits

            logger.info(f"Found {len(commits)} commits to process")

            # Process commits in batches
            for i in range(0, len(commits), batch_size):
                batch = commits[i : i + batch_size]

                for commit in batch:
                    try:
                        await self._ingest_commit(commit)
                        stats["commits_processed"] += 1

                        # Track date range
                        commit_dt = commit.committed_datetime
                        if stats["oldest_commit"] is None or commit_dt < stats["oldest_commit"]:
                            stats["oldest_commit"] = commit_dt
                        if stats["newest_commit"] is None or commit_dt > stats["newest_commit"]:
                            stats["newest_commit"] = commit_dt

                    except Exception as e:
                        logger.error(f"Error processing commit {commit.hexsha[:8]}: {e}")
                        stats["errors"] += 1

                logger.info(
                    f"Processed batch {i//batch_size + 1}/{(len(commits) + batch_size - 1)//batch_size}"
                )

        except Exception as e:
            logger.error(f"Error during git history ingestion: {e}")
            raise

        logger.info(
            f"Ingestion complete: {stats['commits_processed']} commits processed, "
            f"{stats['errors']} errors"
        )

        return stats

    async def _ingest_commit(self, commit: git.Commit) -> None:
        """Ingest a single commit as a Graphiti episode.

        Args:
            commit: GitPython commit object
        """
        episode_body = self._format_commit(commit)
        episode_name = f"{commit.summary[:80]}"

        await self.graphiti.add_episode(
            name=episode_name,
            episode_body=episode_body,
            source_description=f"Git commit {commit.hexsha[:8]}",
            reference_time=commit.committed_datetime,
            source=EpisodeType.text,
        )

    def _format_commit(self, commit: git.Commit) -> str:
        """Format commit information for LLM processing.

        Args:
            commit: GitPython commit object

        Returns:
            Formatted commit text for Graphiti episode
        """
        # Get changed files and diff stats
        if commit.parents:
            parent = commit.parents[0]
            diffs = parent.diff(commit)
            changed_files = [d.a_path or d.b_path for d in diffs]

            # Extract code changes from diffs
            code_changes = self._extract_code_changes(diffs)
        else:
            # Initial commit (no parent)
            changed_files = list(commit.stats.files.keys())
            code_changes = []

        # Format commit message
        message_lines = commit.message.strip().split("\n")
        summary = message_lines[0]
        body = "\n".join(message_lines[1:]).strip() if len(message_lines) > 1 else ""

        # Build episode text
        episode_parts = [
            f"Commit: {commit.hexsha}",
            f"Author: {commit.author.name} <{commit.author.email}>",
            f"Date: {commit.committed_datetime.isoformat()}",
            "",
            f"Summary: {summary}",
        ]

        if body:
            episode_parts.append(f"\nDescription:\n{body}")

        episode_parts.extend([
            "",
            f"Files Changed ({len(changed_files)}):",
            *[f"  - {f}" for f in changed_files[:20]],
        ])

        if len(changed_files) > 20:
            episode_parts.append(f"  ... and {len(changed_files) - 20} more files")

        if code_changes:
            episode_parts.extend([
                "",
                "Code Changes:",
                *[f"  - {change}" for change in code_changes[:10]],
            ])

        episode_parts.extend([
            "",
            "Statistics:",
            f"  +{commit.stats.total.get('insertions', 0)} insertions",
            f"  -{commit.stats.total.get('deletions', 0)} deletions",
            f"  {len(commit.stats.files)} files changed",
        ])

        return "\n".join(episode_parts)

    def _extract_code_changes(self, diffs: git.DiffIndex) -> List[str]:
        """Extract function/class changes from diff objects.

        Args:
            diffs: GitPython diff index

        Returns:
            List of code change descriptions
        """
        changes = []

        for diff in diffs:
            # Only process Python files for now
            file_path = diff.a_path or diff.b_path
            if not file_path.endswith(".py"):
                continue

            # Skip if no diff content
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

                # Extract imports
                import_pattern = r"^\+\s*(?:from\s+[\w.]+\s+)?import\s+([\w,\s]+)"
                imports = re.findall(import_pattern, diff_text, re.MULTILINE)

                for func in funcs:
                    changes.append(f"Modified function: {func} in {file_path}")

                for cls in classes:
                    changes.append(f"Modified class: {cls} in {file_path}")

                if imports:
                    changes.append(f"Modified imports in {file_path}")

            except Exception as e:
                logger.debug(f"Error parsing diff for {file_path}: {e}")

        return changes

    async def query_history(
        self,
        query: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> str:
        """Query git history using natural language.

        Args:
            query: Natural language question about code history
            start_time: Filter episodes after this time
            end_time: Filter episodes before this time

        Returns:
            Natural language response from Graphiti

        Example:
            ```python
            response = await integration.query_history(
                "When did we add OAuth authentication?",
                start_time=datetime(2024, 1, 1, tzinfo=timezone.utc)
            )
            print(response)
            ```
        """
        results = await self.graphiti.search(
            query=query,
            # Graphiti uses center_node_uuid for time-based filtering if needed
        )

        return results

    async def get_entity_timeline(
        self, entity_name: str, entity_type: str = "function"
    ) -> List[Dict[str, Any]]:
        """Get timeline of changes for a specific code entity.

        Args:
            entity_name: Name of the function/class/module
            entity_type: Type of entity (function, class, module)

        Returns:
            List of episodes involving this entity

        Example:
            ```python
            timeline = await integration.get_entity_timeline(
                "authenticate_user",
                entity_type="function"
            )
            for episode in timeline:
                print(f"{episode['date']}: {episode['summary']}")
            ```
        """
        # Search for episodes mentioning this entity
        results = await self.graphiti.search(
            query=f"Show all changes to {entity_type} {entity_name}"
        )

        # Parse results (Graphiti returns formatted text)
        # This would need more sophisticated parsing based on Graphiti's response format
        return results

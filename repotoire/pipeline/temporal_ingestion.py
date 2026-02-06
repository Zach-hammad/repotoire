"""Temporal ingestion pipeline for Git history tracking.

This module extends the base ingestion pipeline to support temporal code analysis
by tracking code snapshots across Git commits.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from repotoire.graph import FalkorDBClient
from repotoire.integrations.git import GitRepository
from repotoire.logging_config import get_logger
from repotoire.models import (
    Entity,
    GitCommit,
    Relationship,
    RelationshipType,
    SessionEntity,
)
from repotoire.pipeline.ingestion import IngestionPipeline

logger = get_logger(__name__)


class TemporalIngestionPipeline:
    """Ingest code with Git history for temporal analysis.

    Extends standard ingestion to track code evolution over time by creating
    Session nodes for each commit and linking entities to their temporal snapshots.

    Example:
        >>> pipeline = TemporalIngestionPipeline(
        ...     repo_path="/path/to/repo",
        ...     graph_client=client
        ... )
        >>> pipeline.ingest_with_history(
        ...     strategy="recent",
        ...     max_commits=10
        ... )
    """

    def __init__(
        self,
        repo_path: str,
        graph_client: FalkorDBClient,
        language: str = "python",
        ignore_patterns: Optional[List[str]] = None,
        generate_clues: bool = False,
    ):
        """Initialize temporal ingestion pipeline.

        Args:
            repo_path: Path to Git repository
            graph_client: FalkorDB database client
            language: Programming language (default: python)
            ignore_patterns: Additional glob patterns to ignore
            generate_clues: Whether to generate semantic clues
        """
        self.repo_path = Path(repo_path).resolve()
        self.graph_client = graph_client
        self.language = language
        self.ignore_patterns = ignore_patterns
        self.generate_clues = generate_clues

        # Initialize Git repository
        self.git_repo = GitRepository(str(self.repo_path))

        logger.info(f"Initialized temporal ingestion for {self.repo_path}")

    def ingest_with_history(
        self,
        strategy: str = "recent",
        max_commits: int = 10,
        since: Optional[datetime] = None,
        branch: str = "HEAD",
        skip_merges: bool = False,
    ) -> dict:
        """Ingest code with Git history.

        Args:
            strategy: Commit selection strategy ("recent", "all", "milestones")
            max_commits: Maximum commits to analyze
            since: Optional start date for commits
            branch: Git branch to analyze
            skip_merges: Whether to skip merge commits

        Returns:
            Summary dict with counts and statistics

        Example:
            >>> result = pipeline.ingest_with_history(
            ...     strategy="recent",
            ...     max_commits=5
            ... )
            >>> result["sessions_created"] <= 5
            True
        """
        logger.info(f"Starting temporal ingestion with strategy='{strategy}', max_commits={max_commits}")

        # Select commits based on strategy
        commits = self._select_commits(strategy, max_commits, since, branch, skip_merges)

        if not commits:
            logger.warning("No commits found to analyze")
            return {
                "sessions_created": 0,
                "entities_created": 0,
                "relationships_created": 0,
            }

        logger.info(f"Selected {len(commits)} commits for analysis")

        sessions_created = 0
        total_entities = 0
        total_relationships = 0

        # Process each commit
        for i, commit in enumerate(commits, 1):
            logger.info(f"[{i}/{len(commits)}] Processing commit {commit.short_hash}: {commit.message[:60]}")

            try:
                # Create session node
                session = self._create_session(commit)
                sessions_created += 1

                # Ingest code snapshot at this commit
                entities, relationships = self._ingest_commit_snapshot(commit, session)

                total_entities += len(entities)
                total_relationships += len(relationships)

                logger.debug(
                    f"  Created {len(entities)} entities, {len(relationships)} relationships"
                )

            except Exception as e:
                logger.error(f"Failed to process commit {commit.short_hash}: {e}", exc_info=True)
                continue

        logger.info(
            f"✓ Temporal ingestion complete: {sessions_created} sessions, "
            f"{total_entities} entities, {total_relationships} relationships"
        )

        return {
            "sessions_created": sessions_created,
            "entities_created": total_entities,
            "relationships_created": total_relationships,
            "commits_processed": len(commits),
        }

    def _select_commits(
        self,
        strategy: str,
        max_commits: int,
        since: Optional[datetime],
        branch: str,
        skip_merges: bool,
    ) -> List[GitCommit]:
        """Select commits based on strategy.

        Args:
            strategy: Selection strategy
            max_commits: Maximum number of commits
            since: Optional start date
            branch: Branch name
            skip_merges: Skip merge commits

        Returns:
            List of selected commits
        """
        if strategy == "recent":
            # Last N commits
            return self.git_repo.get_commit_history(
                branch=branch,
                max_commits=max_commits,
                skip_merges=skip_merges,
            )

        elif strategy == "milestones":
            # Tagged commits only
            tagged = self.git_repo.get_tagged_commits()
            return tagged[:max_commits]

        elif strategy == "all":
            # All commits (expensive!)
            return self.git_repo.get_commit_history(
                since=since,
                branch=branch,
                max_commits=max_commits,
                skip_merges=skip_merges,
            )

        else:
            logger.warning(f"Unknown strategy '{strategy}', defaulting to 'recent'")
            return self.git_repo.get_commit_history(
                branch=branch,
                max_commits=max_commits,
                skip_merges=skip_merges,
            )

    def _create_session(self, commit: GitCommit) -> SessionEntity:
        """Create Session entity for a commit.

        Args:
            commit: Git commit information

        Returns:
            SessionEntity created in the database
        """
        session = SessionEntity(
            name=commit.short_hash,
            qualified_name=f"session::{commit.hash}",
            file_path=str(self.repo_path),
            line_start=0,
            line_end=0,
            commit_hash=commit.hash,
            commit_message=commit.message,
            author=commit.author,
            author_email=commit.author_email,
            committed_at=commit.committed_at,
            branch=commit.branch,
            parent_hashes=commit.parent_hashes,
            files_changed=commit.stats.get("files_changed", 0),
            insertions=commit.stats.get("insertions", 0),
            deletions=commit.stats.get("deletions", 0),
        )

        # Create Session node in Neo4j
        self.graph_client.create_node(session)

        # Create PARENT_OF relationships to parent commits
        for parent_hash in commit.parent_hashes:
            parent_qualified_name = f"session::{parent_hash}"

            # Create relationship (parent -> current)
            rel = Relationship(
                source_id=parent_qualified_name,
                target_id=session.qualified_name,
                rel_type=RelationshipType.PARENT_OF,
                properties={
                    "committed_at": commit.committed_at.isoformat(),
                }
            )

            try:
                self.graph_client.create_relationship(rel)
            except Exception as e:
                logger.debug(f"Could not create PARENT_OF relationship: {e}")

        return session

    def _ingest_commit_snapshot(
        self,
        commit: GitCommit,
        session: SessionEntity,
    ) -> tuple[List[Entity], List[Relationship]]:
        """Ingest code snapshot at a specific commit.

        This creates a standard ingestion pipeline at a specific commit,
        then links entities to the session.

        Args:
            commit: Git commit to analyze
            session: Session entity for this commit

        Returns:
            Tuple of (entities created, relationships created)
        """
        # Create a standard ingestion pipeline
        # Note: For MVP, we'll use the current working directory
        # In production, you'd want to checkout the commit or use git show
        pipeline = IngestionPipeline(
            repo_path=str(self.repo_path),
            graph_client=self.graph_client,
            language=self.language,
            ignore_patterns=self.ignore_patterns,
            generate_clues=self.generate_clues,
        )

        # Run ingestion
        result = pipeline.ingest()

        # Create CONTAINS_SNAPSHOT relationships
        # Link session to entities created in this ingestion
        # Note: This is simplified for MVP - in production you'd track which
        # entities belong to which session more carefully
        relationships_created = []

        # For changed files in this commit, create MODIFIED relationships
        for file_path in commit.changed_files:
            file_qualified_name = file_path

            rel = Relationship(
                source_id=session.qualified_name,
                target_id=file_qualified_name,
                rel_type=RelationshipType.MODIFIED,
                properties={
                    "change_type": "modified",  # could be added, modified, deleted
                    "committed_at": commit.committed_at.isoformat(),
                }
            )

            try:
                self.graph_client.create_relationship(rel)
                relationships_created.append(rel)
            except Exception as e:
                logger.debug(f"Could not create MODIFIED relationship for {file_path}: {e}")

        return ([], relationships_created)  # Entities are created by the pipeline

    def analyze_file_history(
        self,
        file_path: str,
        max_commits: int = 50
    ) -> List[dict]:
        """Analyze how a specific file evolved over time.

        Args:
            file_path: Path to file relative to repository root
            max_commits: Maximum commits to analyze

        Returns:
            List of dicts with commit info and file metrics

        Example:
            >>> history = pipeline.analyze_file_history("src/main.py", max_commits=10)
            >>> len(history) <= 10
            True
        """
        commits = self.git_repo.get_file_history(file_path, max_commits)

        history = []
        for commit in commits:
            # Get file content at this commit
            content = self.git_repo.get_file_at_commit(file_path, commit.hash)

            if content is None:
                continue

            # Calculate basic metrics
            loc = len([line for line in content.splitlines() if line.strip()])

            history.append({
                "commit_hash": commit.hash,
                "short_hash": commit.short_hash,
                "message": commit.message,
                "author": commit.author,
                "committed_at": commit.committed_at,
                "loc": loc,
                "content_size": len(content),
            })

        return history

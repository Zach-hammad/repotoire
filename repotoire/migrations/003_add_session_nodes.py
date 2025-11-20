"""Add Session nodes for temporal code tracking.

This migration adds support for tracking code evolution over time by creating
Session nodes that represent snapshots of the codebase at specific Git commits.
"""

from repotoire.migrations.migration import Migration
from repotoire.graph.client import Neo4jClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class AddSessionNodesMigration(Migration):
    """Add Session nodes and temporal relationships for Git tracking."""

    @property
    def version(self) -> int:
        return 3

    @property
    def description(self) -> str:
        return "Add Session nodes for temporal code tracking and Git history analysis"

    def validate(self, client: Neo4jClient) -> bool:
        """Validate that base schema exists before applying this migration.

        Args:
            client: Neo4j database client

        Returns:
            True if validation passes, False otherwise
        """
        # Check that File constraint exists (from migration 001)
        result = client.execute_query("""
            SHOW CONSTRAINTS
            YIELD name, type
            WHERE name = 'file_path_unique'
            RETURN count(*) as count
        """)

        if not result or result[0]["count"] == 0:
            logger.error("Base schema not found. Run migration 001 first.")
            return False

        logger.info("Validation passed - base schema exists")
        return True

    def up(self, client: Neo4jClient) -> None:
        """Apply migration: create Session node constraints and indexes.

        Creates:
        - Unique constraint on Session.commitHash
        - Indexes for temporal queries (committedAt, branch, author)
        - Indexes for relationship properties
        """
        logger.info("Creating Session node constraints and indexes...")

        # Create unique constraint on Session.commitHash
        client.execute_query("""
            CREATE CONSTRAINT session_commit_hash_unique IF NOT EXISTS
            FOR (s:Session)
            REQUIRE s.commitHash IS UNIQUE
        """)
        logger.debug("Created constraint: session_commit_hash_unique")

        # Create indexes for Session node properties
        indexes = [
            ("session_committed_at_idx", "committedAt"),
            ("session_branch_idx", "branch"),
            ("session_author_idx", "author"),
            ("session_author_email_idx", "authorEmail"),
        ]

        for index_name, property_name in indexes:
            client.execute_query(f"""
                CREATE INDEX {index_name} IF NOT EXISTS
                FOR (s:Session)
                ON (s.{property_name})
            """)
            logger.debug(f"Created index: {index_name}")

        # Create compound index for temporal range queries
        client.execute_query("""
            CREATE INDEX session_branch_date_idx IF NOT EXISTS
            FOR (s:Session)
            ON (s.branch, s.committedAt)
        """)
        logger.debug("Created compound index: session_branch_date_idx")

        logger.info("✓ Session nodes schema created successfully")

    def down(self, client: Neo4jClient) -> None:
        """Rollback migration: drop Session constraints and indexes.

        Warning: This will delete all Session nodes and their relationships!
        """
        logger.warning("Rolling back Session nodes migration...")

        # Drop all Session nodes and relationships
        result = client.execute_query("""
            MATCH (s:Session)
            DETACH DELETE s
            RETURN count(s) as deleted_count
        """)

        deleted_count = result[0]["deleted_count"] if result else 0
        logger.info(f"Deleted {deleted_count} Session nodes")

        # Drop indexes
        indexes = [
            "session_committed_at_idx",
            "session_branch_idx",
            "session_author_idx",
            "session_author_email_idx",
            "session_branch_date_idx",
        ]

        for index_name in indexes:
            try:
                client.execute_query(f"DROP INDEX {index_name} IF EXISTS")
                logger.debug(f"Dropped index: {index_name}")
            except Exception as e:
                logger.warning(f"Could not drop index {index_name}: {e}")

        # Drop constraint
        try:
            client.execute_query("DROP CONSTRAINT session_commit_hash_unique IF EXISTS")
            logger.debug("Dropped constraint: session_commit_hash_unique")
        except Exception as e:
            logger.warning(f"Could not drop constraint session_commit_hash_unique: {e}")

        logger.info("✓ Session nodes migration rolled back")

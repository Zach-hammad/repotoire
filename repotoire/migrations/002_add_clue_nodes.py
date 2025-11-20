"""Add Clue nodes for AI-generated semantic summaries."""

import re
from repotoire.migrations.migration import Migration, MigrationError
from repotoire.graph import Neo4jClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class AddClueNodesMigration(Migration):
    """Add Clue node type with indexes and constraints."""

    @property
    def version(self) -> int:
        return 2

    @property
    def description(self) -> str:
        return "Add Clue nodes for AI-generated semantic code summaries and insights"

    def validate(self, client: Neo4jClient) -> bool:
        """Validate database is accessible and has version 1 schema."""
        try:
            # Check database connectivity
            result = client.execute_query("RETURN 1 AS test")
            if not result or result[0]["test"] != 1:
                raise MigrationError("Database connectivity check failed")

            # Verify base schema exists (from migration 001)
            check_query = """
            SHOW CONSTRAINTS
            YIELD name
            WHERE name = 'file_path_unique'
            RETURN count(name) AS count
            """
            result = client.execute_query(check_query)

            if not result or result[0]["count"] == 0:
                raise MigrationError(
                    "Base schema not found. Please run migration 001 first."
                )

            return True

        except Exception as e:
            raise MigrationError(f"Validation failed: {e}")

    def up(self, client: Neo4jClient) -> None:
        """Add Clue node constraints and indexes."""
        logger.info("Adding Clue node schema")

        # Uniqueness constraint for Clue nodes
        constraints = [
            "CREATE CONSTRAINT clue_qualified_name_unique IF NOT EXISTS FOR (c:Clue) REQUIRE c.qualifiedName IS UNIQUE",
        ]

        for constraint in constraints:
            try:
                client.execute_query(constraint)
                logger.debug(f"Created constraint: {constraint[:50]}...")
            except Exception as e:
                logger.warning(f"Could not create constraint: {e}")

        # Performance indexes for Clue nodes
        indexes = [
            # Basic indexes
            "CREATE INDEX clue_qualified_name_idx IF NOT EXISTS FOR (c:Clue) ON (c.qualifiedName)",
            "CREATE INDEX clue_type_idx IF NOT EXISTS FOR (c:Clue) ON (c.clue_type)",
            "CREATE INDEX clue_target_entity_idx IF NOT EXISTS FOR (c:Clue) ON (c.target_entity)",
            "CREATE INDEX clue_generated_by_idx IF NOT EXISTS FOR (c:Clue) ON (c.generated_by)",
            "CREATE INDEX clue_confidence_idx IF NOT EXISTS FOR (c:Clue) ON (c.confidence)",
            "CREATE INDEX clue_file_path_idx IF NOT EXISTS FOR (c:Clue) ON (c.filePath)",
            # Composite indexes for common queries
            "CREATE INDEX clue_type_confidence_idx IF NOT EXISTS FOR (c:Clue) ON (c.clue_type, c.confidence)",
            "CREATE INDEX clue_target_generated_idx IF NOT EXISTS FOR (c:Clue) ON (c.target_entity, c.generated_by)",
            # Full-text search indexes for semantic queries
            "CREATE FULLTEXT INDEX clue_summary_idx IF NOT EXISTS FOR (c:Clue) ON EACH [c.summary]",
            "CREATE FULLTEXT INDEX clue_detailed_idx IF NOT EXISTS FOR (c:Clue) ON EACH [c.detailed_explanation]",
            "CREATE FULLTEXT INDEX clue_keywords_idx IF NOT EXISTS FOR (c:Clue) ON EACH [c.keywords]",
        ]

        for index in indexes:
            try:
                client.execute_query(index)
                logger.debug(f"Created index: {index[:50]}...")
            except Exception as e:
                logger.warning(f"Could not create index: {e}")

        logger.info("Clue node schema created successfully")

    def down(self, client: Neo4jClient) -> None:
        """Remove Clue node constraints and indexes."""
        logger.info("Removing Clue node schema")

        # Validate name is safe (alphanumeric, underscore, hyphen only)
        def is_safe_name(name: str) -> bool:
            return bool(re.match(r'^[a-zA-Z0-9_-]+$', name))

        # Drop Clue-specific constraints
        drop_constraints_query = """
        SHOW CONSTRAINTS
        YIELD name
        WHERE name STARTS WITH 'clue_'
        RETURN name
        """
        try:
            constraints = client.execute_query(drop_constraints_query)
            for record in constraints:
                name = record["name"]
                if is_safe_name(name):
                    client.execute_query(f"DROP CONSTRAINT {name}")
                    logger.debug(f"Dropped constraint: {name}")
                else:
                    logger.warning(f"Skipping constraint with unsafe name: {name}")
        except Exception as e:
            logger.warning(f"Error dropping Clue constraints: {e}")

        # Drop Clue-specific indexes
        drop_indexes_query = """
        SHOW INDEXES
        YIELD name
        WHERE name STARTS WITH 'clue_'
        RETURN name
        """
        try:
            indexes = client.execute_query(drop_indexes_query)
            for record in indexes:
                name = record["name"]
                if is_safe_name(name):
                    client.execute_query(f"DROP INDEX {name}")
                    logger.debug(f"Dropped index: {name}")
                else:
                    logger.warning(f"Skipping index with unsafe name: {name}")
        except Exception as e:
            logger.warning(f"Error dropping Clue indexes: {e}")

        # Delete all Clue nodes (cascade delete relationships)
        delete_clues_query = """
        MATCH (c:Clue)
        DETACH DELETE c
        """
        try:
            client.execute_query(delete_clues_query)
            logger.info("Deleted all Clue nodes")
        except Exception as e:
            logger.warning(f"Error deleting Clue nodes: {e}")

        logger.info("Clue node schema removed successfully")

"""Initial schema migration - captures current schema state."""

import re
from repotoire.migrations.migration import Migration, MigrationError
from repotoire.graph import Neo4jClient
from repotoire.logging_config import get_logger

logger = get_logger(__name__)


class InitialSchemaMigration(Migration):
    """Create initial database schema with constraints and indexes."""

    @property
    def version(self) -> int:
        return 1

    @property
    def description(self) -> str:
        return "Initial schema with constraints and indexes for File, Module, Class, Function, Concept nodes"

    def validate(self, client: Neo4jClient) -> bool:
        """Validate database is accessible and empty or has no conflicting schema."""
        try:
            # Check database connectivity
            result = client.execute_query("RETURN 1 AS test")
            if not result or result[0]["test"] != 1:
                raise MigrationError("Database connectivity check failed")

            # Check if schema already exists
            check_query = "SHOW CONSTRAINTS YIELD name RETURN count(name) AS count"
            result = client.execute_query(check_query)

            constraint_count = result[0]["count"] if result else 0
            if constraint_count > 0:
                logger.warning(f"Database already has {constraint_count} constraints - migration may conflict")

            return True

        except Exception as e:
            raise MigrationError(f"Validation failed: {e}")

    def up(self, client: Neo4jClient) -> None:
        """Create initial schema constraints and indexes."""
        logger.info("Creating initial schema constraints and indexes")

        # Uniqueness constraints
        constraints = [
            "CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.filePath IS UNIQUE",
            "CREATE CONSTRAINT module_qualified_name_unique IF NOT EXISTS FOR (m:Module) REQUIRE m.qualifiedName IS UNIQUE",
            "CREATE CONSTRAINT class_qualified_name_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.qualifiedName IS UNIQUE",
            "CREATE CONSTRAINT function_qualified_name_unique IF NOT EXISTS FOR (f:Function) REQUIRE f.qualifiedName IS UNIQUE",
        ]

        for constraint in constraints:
            try:
                client.execute_query(constraint)
                logger.debug(f"Created constraint: {constraint[:50]}...")
            except Exception as e:
                logger.warning(f"Could not create constraint: {e}")

        # Performance indexes
        indexes = [
            # Basic indexes
            "CREATE INDEX file_path_idx IF NOT EXISTS FOR (f:File) ON (f.filePath)",
            "CREATE INDEX file_language_idx IF NOT EXISTS FOR (f:File) ON (f.language)",
            "CREATE INDEX module_name_idx IF NOT EXISTS FOR (m:Module) ON (m.qualifiedName)",
            "CREATE INDEX module_external_idx IF NOT EXISTS FOR (m:Module) ON (m.is_external)",
            "CREATE INDEX class_name_idx IF NOT EXISTS FOR (c:Class) ON (c.qualifiedName)",
            "CREATE INDEX function_name_idx IF NOT EXISTS FOR (f:Function) ON (f.qualifiedName)",
            "CREATE INDEX concept_name_idx IF NOT EXISTS FOR (c:Concept) ON (c.name)",
            "CREATE INDEX attribute_name_idx IF NOT EXISTS FOR (a:Attribute) ON (a.name)",
            "CREATE INDEX variable_name_idx IF NOT EXISTS FOR (v:Variable) ON (v.name)",
            # Function and class name pattern matching (for STARTS WITH queries)
            "CREATE INDEX function_simple_name_idx IF NOT EXISTS FOR (f:Function) ON (f.name)",
            "CREATE INDEX class_simple_name_idx IF NOT EXISTS FOR (c:Class) ON (c.name)",
            # File exports for dead code detection
            "CREATE INDEX file_exports_idx IF NOT EXISTS FOR (f:File) ON (f.exports)",
            # Full-text search indexes
            "CREATE FULLTEXT INDEX function_docstring_idx IF NOT EXISTS FOR (f:Function) ON EACH [f.docstring]",
            "CREATE FULLTEXT INDEX class_docstring_idx IF NOT EXISTS FOR (c:Class) ON EACH [c.docstring]",
            # Composite indexes for detector queries
            "CREATE INDEX class_complexity_idx IF NOT EXISTS FOR (c:Class) ON (c.complexity, c.is_abstract)",
            "CREATE INDEX function_complexity_idx IF NOT EXISTS FOR (f:Function) ON (f.complexity, f.is_async)",
            "CREATE INDEX file_language_loc_idx IF NOT EXISTS FOR (f:File) ON (f.language, f.loc)",
            # Relationship property indexes
            "CREATE INDEX inherits_order_idx IF NOT EXISTS FOR ()-[r:INHERITS]-() ON (r.order)",
        ]

        for index in indexes:
            try:
                client.execute_query(index)
                logger.debug(f"Created index: {index[:50]}...")
            except Exception as e:
                logger.warning(f"Could not create index: {e}")

        logger.info("Initial schema created successfully")

    def down(self, client: Neo4jClient) -> None:
        """Drop all schema constraints and indexes."""
        logger.info("Rolling back initial schema")

        # Validate name is safe (alphanumeric, underscore, hyphen only)
        def is_safe_name(name: str) -> bool:
            return bool(re.match(r'^[a-zA-Z0-9_-]+$', name))

        # Drop all constraints
        drop_constraints_query = """
        SHOW CONSTRAINTS
        YIELD name
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
            logger.warning(f"Error dropping constraints: {e}")

        # Drop all indexes (except system indexes)
        drop_indexes_query = """
        SHOW INDEXES
        YIELD name
        WHERE name <> 'node_label_index' AND name <> 'relationship_type_index'
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
            logger.warning(f"Error dropping indexes: {e}")

        logger.info("Initial schema rolled back successfully")

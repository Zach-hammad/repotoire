"""Initial schema migration - captures current schema state."""

import re
from repotoire.migrations.migration import Migration, MigrationError
from repotoire.graph import FalkorDBClient
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

    def validate(self, client: FalkorDBClient) -> bool:
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

    def up(self, client: FalkorDBClient) -> None:
        """Create initial schema constraints and indexes."""
        logger.info("Creating initial schema constraints and indexes")

        # Uniqueness constraints (synced with schema.py)
        constraints = [
            "CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.filePath IS UNIQUE",
            "CREATE CONSTRAINT module_qualified_name_unique IF NOT EXISTS FOR (m:Module) REQUIRE m.qualifiedName IS UNIQUE",
            "CREATE CONSTRAINT class_qualified_name_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.qualifiedName IS UNIQUE",
            "CREATE CONSTRAINT function_qualified_name_unique IF NOT EXISTS FOR (f:Function) REQUIRE f.qualifiedName IS UNIQUE",
            # Rule engine constraints (REPO-125)
            "CREATE CONSTRAINT rule_id_unique IF NOT EXISTS FOR (r:Rule) REQUIRE r.id IS UNIQUE",
            # Cross-detector collaboration constraints (REPO-151 Phase 2)
            "CREATE CONSTRAINT detector_metadata_id_unique IF NOT EXISTS FOR (d:DetectorMetadata) REQUIRE d.id IS UNIQUE",
        ]

        for constraint in constraints:
            try:
                client.execute_query(constraint)
                logger.debug(f"Created constraint: {constraint[:50]}...")
            except Exception as e:
                logger.warning(f"Could not create constraint: {e}")

        # Performance indexes (synced with schema.py)
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
            # Composite indexes leveraging enhanced properties (FAL-91)
            "CREATE INDEX file_language_test_idx IF NOT EXISTS FOR (f:File) ON (f.language, f.is_test)",
            "CREATE INDEX file_test_module_idx IF NOT EXISTS FOR (f:File) ON (f.is_test, f.module_path)",
            "CREATE INDEX function_method_static_idx IF NOT EXISTS FOR (f:Function) ON (f.is_method, f.is_static)",
            "CREATE INDEX function_method_property_idx IF NOT EXISTS FOR (f:Function) ON (f.is_method, f.is_property)",
            "CREATE INDEX class_dataclass_exception_idx IF NOT EXISTS FOR (c:Class) ON (c.is_dataclass, c.is_exception)",
            "CREATE INDEX function_async_yield_idx IF NOT EXISTS FOR (f:Function) ON (f.is_async, f.has_yield)",
            # Relationship property indexes for query performance
            "CREATE INDEX imports_module_idx IF NOT EXISTS FOR ()-[r:IMPORTS]-() ON (r.module)",
            "CREATE INDEX calls_line_number_idx IF NOT EXISTS FOR ()-[r:CALLS]-() ON (r.line_number)",
            "CREATE INDEX inherits_order_idx IF NOT EXISTS FOR ()-[r:INHERITS]-() ON (r.order)",
            # Enhanced node property indexes (FAL-90)
            "CREATE INDEX file_is_test_idx IF NOT EXISTS FOR (f:File) ON (f.is_test)",
            "CREATE INDEX file_module_path_idx IF NOT EXISTS FOR (f:File) ON (f.module_path)",
            "CREATE INDEX class_is_dataclass_idx IF NOT EXISTS FOR (c:Class) ON (c.is_dataclass)",
            "CREATE INDEX class_is_exception_idx IF NOT EXISTS FOR (c:Class) ON (c.is_exception)",
            "CREATE INDEX class_nesting_level_idx IF NOT EXISTS FOR (c:Class) ON (c.nesting_level)",
            "CREATE INDEX function_is_method_idx IF NOT EXISTS FOR (f:Function) ON (f.is_method)",
            "CREATE INDEX function_is_static_idx IF NOT EXISTS FOR (f:Function) ON (f.is_static)",
            "CREATE INDEX function_is_property_idx IF NOT EXISTS FOR (f:Function) ON (f.is_property)",
            "CREATE INDEX function_has_return_idx IF NOT EXISTS FOR (f:Function) ON (f.has_return)",
            "CREATE INDEX function_has_yield_idx IF NOT EXISTS FOR (f:Function) ON (f.has_yield)",
            # Rule engine indexes (REPO-125)
            "CREATE INDEX rule_last_used_idx IF NOT EXISTS FOR (r:Rule) ON (r.lastUsed)",
            "CREATE INDEX rule_access_count_idx IF NOT EXISTS FOR (r:Rule) ON (r.accessCount)",
            "CREATE INDEX rule_priority_idx IF NOT EXISTS FOR (r:Rule) ON (r.userPriority)",
            "CREATE INDEX rule_enabled_idx IF NOT EXISTS FOR (r:Rule) ON (r.enabled)",
            "CREATE INDEX rule_severity_idx IF NOT EXISTS FOR (r:Rule) ON (r.severity)",
            "CREATE INDEX rule_hot_rules_idx IF NOT EXISTS FOR (r:Rule) ON (r.enabled, r.lastUsed, r.userPriority)",
            # Cross-detector collaboration indexes (REPO-151 Phase 2)
            "CREATE INDEX detector_metadata_detector_idx IF NOT EXISTS FOR (d:DetectorMetadata) ON (d.detector)",
            "CREATE INDEX detector_metadata_timestamp_idx IF NOT EXISTS FOR (d:DetectorMetadata) ON (d.timestamp)",
            "CREATE INDEX flagged_by_severity_idx IF NOT EXISTS FOR ()-[r:FLAGGED_BY]-() ON (r.severity)",
            "CREATE INDEX flagged_by_confidence_idx IF NOT EXISTS FOR ()-[r:FLAGGED_BY]-() ON (r.confidence)",
            # Contextual retrieval indexes (REPO-242)
            "CREATE INDEX function_semantic_context_idx IF NOT EXISTS FOR (f:Function) ON (f.semantic_context)",
            "CREATE INDEX class_semantic_context_idx IF NOT EXISTS FOR (c:Class) ON (c.semantic_context)",
            "CREATE INDEX file_semantic_context_idx IF NOT EXISTS FOR (f:File) ON (f.semantic_context)",
            # Multi-tenant repo isolation indexes (REPO-391)
            "CREATE INDEX file_repo_id_idx IF NOT EXISTS FOR (f:File) ON (f.repoId)",
            "CREATE INDEX function_repo_id_idx IF NOT EXISTS FOR (f:Function) ON (f.repoId)",
            "CREATE INDEX class_repo_id_idx IF NOT EXISTS FOR (c:Class) ON (c.repoId)",
            "CREATE INDEX module_repo_id_idx IF NOT EXISTS FOR (m:Module) ON (m.repoId)",
            "CREATE INDEX file_repo_path_idx IF NOT EXISTS FOR (f:File) ON (f.repoId, f.filePath)",
            "CREATE INDEX function_repo_name_idx IF NOT EXISTS FOR (f:Function) ON (f.repoId, f.name)",
            "CREATE INDEX class_repo_name_idx IF NOT EXISTS FOR (c:Class) ON (c.repoId, c.name)",
            # Data flow graph indexes for taint tracking (REPO-411)
            "CREATE INDEX flows_to_edge_type_idx IF NOT EXISTS FOR ()-[r:FLOWS_TO]-() ON (r.edge_type)",
            "CREATE INDEX flows_to_source_line_idx IF NOT EXISTS FOR ()-[r:FLOWS_TO]-() ON (r.source_line)",
            "CREATE INDEX flows_to_scope_idx IF NOT EXISTS FOR ()-[r:FLOWS_TO]-() ON (r.scope)",
        ]

        for index in indexes:
            try:
                client.execute_query(index)
                logger.debug(f"Created index: {index[:50]}...")
            except Exception as e:
                logger.warning(f"Could not create index: {e}")

        logger.info("Initial schema created successfully")

    def down(self, client: FalkorDBClient) -> None:
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

"""Graph schema definition and initialization."""

from typing import Optional


class GraphSchema:
    """Manages graph schema creation and constraints.

    Supports both Neo4j and FalkorDB backends.
    """

    # Constraint definitions
    CONSTRAINTS = [
        # Uniqueness constraints
        "CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.filePath IS UNIQUE",
        "CREATE CONSTRAINT module_qualified_name_unique IF NOT EXISTS FOR (m:Module) REQUIRE m.qualifiedName IS UNIQUE",
        "CREATE CONSTRAINT class_qualified_name_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.qualifiedName IS UNIQUE",
        "CREATE CONSTRAINT function_qualified_name_unique IF NOT EXISTS FOR (f:Function) REQUIRE f.qualifiedName IS UNIQUE",
        # Rule engine constraints (REPO-125)
        "CREATE CONSTRAINT rule_id_unique IF NOT EXISTS FOR (r:Rule) REQUIRE r.id IS UNIQUE",
        # Cross-detector collaboration constraints (REPO-151 Phase 2)
        "CREATE CONSTRAINT detector_metadata_id_unique IF NOT EXISTS FOR (d:DetectorMetadata) REQUIRE d.id IS UNIQUE",
    ]

    # Index definitions for performance
    INDEXES = [
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
        # Rule engine indexes (REPO-125) - for time-based priority refresh
        "CREATE INDEX rule_last_used_idx IF NOT EXISTS FOR (r:Rule) ON (r.lastUsed)",
        "CREATE INDEX rule_access_count_idx IF NOT EXISTS FOR (r:Rule) ON (r.accessCount)",
        "CREATE INDEX rule_priority_idx IF NOT EXISTS FOR (r:Rule) ON (r.userPriority)",
        "CREATE INDEX rule_enabled_idx IF NOT EXISTS FOR (r:Rule) ON (r.enabled)",
        "CREATE INDEX rule_severity_idx IF NOT EXISTS FOR (r:Rule) ON (r.severity)",
        # Composite index for hot rule queries (sorted by lastUsed + priority)
        "CREATE INDEX rule_hot_rules_idx IF NOT EXISTS FOR (r:Rule) ON (r.enabled, r.lastUsed, r.userPriority)",
        # Cross-detector collaboration indexes (REPO-151 Phase 2)
        "CREATE INDEX detector_metadata_detector_idx IF NOT EXISTS FOR (d:DetectorMetadata) ON (d.detector)",
        "CREATE INDEX detector_metadata_timestamp_idx IF NOT EXISTS FOR (d:DetectorMetadata) ON (d.timestamp)",
        "CREATE INDEX flagged_by_severity_idx IF NOT EXISTS FOR ()-[r:FLAGGED_BY]-() ON (r.severity)",
        "CREATE INDEX flagged_by_confidence_idx IF NOT EXISTS FOR ()-[r:FLAGGED_BY]-() ON (r.confidence)",
    ]

    # Vector index definitions (labels and index names)
    # Dimensions are configured at runtime via create_vector_indexes()
    VECTOR_INDEX_DEFS = [
        ("Function", "function_embeddings", "f"),
        ("Class", "class_embeddings", "c"),
        ("File", "file_embeddings", "f"),
    ]

    # FalkorDB index definitions (simpler syntax)
    FALKORDB_INDEXES = [
        "CREATE INDEX ON :File(filePath)",
        "CREATE INDEX ON :File(language)",
        "CREATE INDEX ON :Module(qualifiedName)",
        "CREATE INDEX ON :Class(qualifiedName)",
        "CREATE INDEX ON :Function(qualifiedName)",
        "CREATE INDEX ON :Function(name)",
        "CREATE INDEX ON :Class(name)",
    ]

    @staticmethod
    def _neo4j_vector_index_query(
        label: str, index_name: str, alias: str, dimensions: int
    ) -> str:
        """Generate Neo4j vector index creation query.

        Args:
            label: Node label (e.g., "Function")
            index_name: Index name (e.g., "function_embeddings")
            alias: Query alias (e.g., "f")
            dimensions: Vector dimensions (384 for local, 1536 for OpenAI)

        Returns:
            Cypher query string
        """
        return f"""
        CREATE VECTOR INDEX {index_name} IF NOT EXISTS
        FOR ({alias}:{label})
        ON {alias}.embedding
        OPTIONS {{
            indexConfig: {{
                `vector.dimensions`: {dimensions},
                `vector.similarity_function`: 'cosine'
            }}
        }}
        """

    @staticmethod
    def _falkordb_vector_index_query(label: str, alias: str, dimensions: int) -> str:
        """Generate FalkorDB vector index creation query.

        Args:
            label: Node label (e.g., "Function")
            alias: Query alias (e.g., "f")
            dimensions: Vector dimensions (384 for local, 1536 for OpenAI)

        Returns:
            Cypher query string
        """
        return f"""
        CREATE VECTOR INDEX FOR ({alias}:{label})
        ON ({alias}.embedding)
        OPTIONS {{dimension: {dimensions}, similarityFunction: 'cosine'}}
        """

    def __init__(self, client):
        """Initialize schema manager.

        Args:
            client: Neo4j or FalkorDB client instance
        """
        self.client = client
        # Detect if we're using FalkorDB
        self.is_falkordb = type(client).__name__ == "FalkorDBClient"

    def create_constraints(self) -> None:
        """Create all uniqueness constraints."""
        if self.is_falkordb:
            # FalkorDB doesn't support Neo4j-style constraints
            print("Skipping constraints (FalkorDB uses indexes only)")
            return

        for constraint in self.CONSTRAINTS:
            try:
                self.client.execute_query(constraint)
            except Exception as e:
                print(f"Warning: Could not create constraint: {e}")

    def create_indexes(self) -> None:
        """Create all indexes."""
        if self.is_falkordb:
            # Use FalkorDB-specific index syntax
            for index in self.FALKORDB_INDEXES:
                try:
                    self.client.execute_query(index)
                except Exception as e:
                    # Index may already exist
                    pass
            return

        for index in self.INDEXES:
            try:
                self.client.execute_query(index)
            except Exception as e:
                print(f"Warning: Could not create index: {e}")

    def create_vector_indexes(self, dimensions: int = 1536) -> None:
        """Create vector indexes for RAG semantic search.

        Requires Neo4j 5.18+ or FalkorDB with vector support.

        Args:
            dimensions: Vector dimensions (1536 for OpenAI, 384 for local)
        """
        print(f"Creating vector indexes with {dimensions} dimensions...")

        for label, index_name, alias in self.VECTOR_INDEX_DEFS:
            try:
                if self.is_falkordb:
                    query = self._falkordb_vector_index_query(label, alias, dimensions)
                else:
                    query = self._neo4j_vector_index_query(
                        label, index_name, alias, dimensions
                    )
                self.client.execute_query(query)
            except Exception as e:
                # Index may already exist or vector support not enabled
                db_type = "FalkorDB" if self.is_falkordb else "Neo4j 5.18+"
                print(f"Info: Could not create vector index for {label} (requires {db_type}): {e}")

    def initialize(
        self,
        enable_vector_search: bool = False,
        vector_dimensions: int = 1536,
    ) -> None:
        """Initialize complete schema.

        Args:
            enable_vector_search: Whether to create vector indexes for RAG (requires Neo4j 5.18+)
            vector_dimensions: Vector dimensions for embeddings (1536 for OpenAI, 384 for local)
        """
        print("Creating graph schema...")
        self.create_constraints()
        self.create_indexes()

        if enable_vector_search:
            self.create_vector_indexes(dimensions=vector_dimensions)

        print("Schema created successfully!")

    def drop_all(self) -> None:
        """Drop all constraints and indexes. Use with caution!"""
        if self.is_falkordb:
            # FalkorDB: just clear the graph
            print("FalkorDB: Clearing graph (no separate schema management)")
            return

        import re

        # Validate name is safe (alphanumeric, underscore, hyphen only)
        def is_safe_name(name: str) -> bool:
            return bool(re.match(r'^[a-zA-Z0-9_-]+$', name))

        # Drop all constraints
        drop_constraints_query = """
        SHOW CONSTRAINTS
        YIELD name
        RETURN name
        """
        constraints = self.client.execute_query(drop_constraints_query)
        for record in constraints:
            name = record["name"]
            if is_safe_name(name):
                # Safe to use f-string since we validated the name
                self.client.execute_query(f"DROP CONSTRAINT {name}")
            else:
                print(f"Warning: Skipping constraint with unsafe name: {name}")

        # Drop all indexes
        drop_indexes_query = """
        SHOW INDEXES
        YIELD name
        WHERE name <> 'node_label_index' AND name <> 'relationship_type_index'
        RETURN name
        """
        indexes = self.client.execute_query(drop_indexes_query)
        for record in indexes:
            name = record["name"]
            if is_safe_name(name):
                # Safe to use f-string since we validated the name
                self.client.execute_query(f"DROP INDEX {name}")
            else:
                print(f"Warning: Skipping index with unsafe name: {name}")

        print("Schema dropped!")

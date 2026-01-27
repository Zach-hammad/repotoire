"""Graph schema definition and initialization."""

from typing import Optional


class GraphSchema:
    """Manages graph schema creation and constraints.

    Supports both Neo4j and FalkorDB backends.
    """

    # Constraint definitions
    # REPO-600: Updated for multi-tenant isolation
    # Uniqueness is now scoped by tenant_id for data isolation
    CONSTRAINTS = [
        # Legacy single-tenant constraints (kept for backward compatibility in dev/single-tenant mode)
        # These will be replaced by tenant-scoped constraints in production
        "CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.filePath IS UNIQUE",
        "CREATE CONSTRAINT module_qualified_name_unique IF NOT EXISTS FOR (m:Module) REQUIRE m.qualifiedName IS UNIQUE",
        "CREATE CONSTRAINT class_qualified_name_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.qualifiedName IS UNIQUE",
        "CREATE CONSTRAINT function_qualified_name_unique IF NOT EXISTS FOR (f:Function) REQUIRE f.qualifiedName IS UNIQUE",
        # Rule engine constraints (REPO-125)
        "CREATE CONSTRAINT rule_id_unique IF NOT EXISTS FOR (r:Rule) REQUIRE r.id IS UNIQUE",
        # Cross-detector collaboration constraints (REPO-151 Phase 2)
        "CREATE CONSTRAINT detector_metadata_id_unique IF NOT EXISTS FOR (d:DetectorMetadata) REQUIRE d.id IS UNIQUE",
    ]

    # REPO-600: Multi-tenant composite constraints
    # These ensure uniqueness within a tenant while allowing same paths across tenants
    # Note: These are applied separately via initialize() when multi-tenant mode is enabled
    MULTI_TENANT_CONSTRAINTS = [
        # Composite uniqueness: (tenant_id, qualified_name) must be unique
        "CREATE CONSTRAINT file_tenant_path_unique IF NOT EXISTS FOR (f:File) REQUIRE (f.tenantId, f.filePath) IS UNIQUE",
        "CREATE CONSTRAINT module_tenant_qn_unique IF NOT EXISTS FOR (m:Module) REQUIRE (m.tenantId, m.qualifiedName) IS UNIQUE",
        "CREATE CONSTRAINT class_tenant_qn_unique IF NOT EXISTS FOR (c:Class) REQUIRE (c.tenantId, c.qualifiedName) IS UNIQUE",
        "CREATE CONSTRAINT function_tenant_qn_unique IF NOT EXISTS FOR (f:Function) REQUIRE (f.tenantId, f.qualifiedName) IS UNIQUE",
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
        # Contextual retrieval indexes (REPO-242)
        # Index for checking if semantic context exists on entities
        "CREATE INDEX function_semantic_context_idx IF NOT EXISTS FOR (f:Function) ON (f.semantic_context)",
        "CREATE INDEX class_semantic_context_idx IF NOT EXISTS FOR (c:Class) ON (c.semantic_context)",
        "CREATE INDEX file_semantic_context_idx IF NOT EXISTS FOR (f:File) ON (f.semantic_context)",
        # Multi-tenant repo isolation indexes (REPO-391)
        # These enable efficient filtering by repo_id within an org's graph
        "CREATE INDEX file_repo_id_idx IF NOT EXISTS FOR (f:File) ON (f.repoId)",
        "CREATE INDEX function_repo_id_idx IF NOT EXISTS FOR (f:Function) ON (f.repoId)",
        "CREATE INDEX class_repo_id_idx IF NOT EXISTS FOR (c:Class) ON (c.repoId)",
        "CREATE INDEX module_repo_id_idx IF NOT EXISTS FOR (m:Module) ON (m.repoId)",
        # Composite indexes for efficient repo + path/name lookups
        "CREATE INDEX file_repo_path_idx IF NOT EXISTS FOR (f:File) ON (f.repoId, f.filePath)",
        "CREATE INDEX function_repo_name_idx IF NOT EXISTS FOR (f:Function) ON (f.repoId, f.name)",
        "CREATE INDEX class_repo_name_idx IF NOT EXISTS FOR (c:Class) ON (c.repoId, c.name)",
        # Multi-tenant organization isolation indexes (REPO-600)
        # These enable filtering by tenant_id (org_id) for data isolation
        "CREATE INDEX file_tenant_id_idx IF NOT EXISTS FOR (f:File) ON (f.tenantId)",
        "CREATE INDEX function_tenant_id_idx IF NOT EXISTS FOR (f:Function) ON (f.tenantId)",
        "CREATE INDEX class_tenant_id_idx IF NOT EXISTS FOR (c:Class) ON (c.tenantId)",
        "CREATE INDEX module_tenant_id_idx IF NOT EXISTS FOR (m:Module) ON (m.tenantId)",
        "CREATE INDEX variable_tenant_id_idx IF NOT EXISTS FOR (v:Variable) ON (v.tenantId)",
        "CREATE INDEX attribute_tenant_id_idx IF NOT EXISTS FOR (a:Attribute) ON (a.tenantId)",
        "CREATE INDEX concept_tenant_id_idx IF NOT EXISTS FOR (c:Concept) ON (c.tenantId)",
        # Composite indexes for efficient tenant + path/name lookups
        "CREATE INDEX file_tenant_path_idx IF NOT EXISTS FOR (f:File) ON (f.tenantId, f.filePath)",
        "CREATE INDEX function_tenant_name_idx IF NOT EXISTS FOR (f:Function) ON (f.tenantId, f.name)",
        "CREATE INDEX class_tenant_name_idx IF NOT EXISTS FOR (c:Class) ON (c.tenantId, c.name)",
        # Composite tenant + repo indexes for hierarchical filtering
        "CREATE INDEX file_tenant_repo_idx IF NOT EXISTS FOR (f:File) ON (f.tenantId, f.repoId)",
        "CREATE INDEX function_tenant_repo_idx IF NOT EXISTS FOR (f:Function) ON (f.tenantId, f.repoId)",
        "CREATE INDEX class_tenant_repo_idx IF NOT EXISTS FOR (c:Class) ON (c.tenantId, c.repoId)",
        # Data flow graph indexes for taint tracking (REPO-411)
        "CREATE INDEX flows_to_edge_type_idx IF NOT EXISTS FOR ()-[r:FLOWS_TO]-() ON (r.edge_type)",
        "CREATE INDEX flows_to_source_line_idx IF NOT EXISTS FOR ()-[r:FLOWS_TO]-() ON (r.source_line)",
        "CREATE INDEX flows_to_scope_idx IF NOT EXISTS FOR ()-[r:FLOWS_TO]-() ON (r.scope)",
        # Performance optimization indexes (Phase 2)
        # Dead code detection: 3-way filter on usage counts
        "CREATE INDEX function_usage_idx IF NOT EXISTS FOR (f:Function) ON (f.call_count, f.inherit_count, f.use_count)",
        # Extended function complexity index for analysis queries
        "CREATE INDEX function_complexity_return_idx IF NOT EXISTS FOR (f:Function) ON (f.complexity, f.is_async, f.has_return)",
        # Class type filtering for God class detection
        "CREATE INDEX class_type_idx IF NOT EXISTS FOR (c:Class) ON (c.is_dataclass, c.is_abstract)",
        # File filtering for language-specific analysis
        "CREATE INDEX file_lang_external_idx IF NOT EXISTS FOR (f:File) ON (f.language, f.is_external)",
    ]

    # Vector index definitions (labels and index names)
    # Dimensions are configured at runtime via create_vector_indexes()
    VECTOR_INDEX_DEFS = [
        ("Function", "function_embeddings", "f"),
        ("Class", "class_embeddings", "c"),
        ("File", "file_embeddings", "f"),
        ("Commit", "commit_embeddings", "c"),  # Git history RAG
    ]

    # HNSW index tuning parameters for memory optimization
    # Reducing M from 16 to 12 saves ~25% memory with minimal accuracy loss
    # Reducing efConstruction from 200 to 150 speeds up index building
    HNSW_M = 12  # Number of neighbors (default: 16, memory-optimized: 12)
    HNSW_EF_CONSTRUCTION = 150  # Construction quality (default: 200)

    # Full-text index definitions for BM25 hybrid search (REPO-243)
    # These combine multiple fields for comprehensive keyword matching
    FULLTEXT_INDEX_DEFS = [
        # Functions: name, docstring, source_code for comprehensive search
        """
        CREATE FULLTEXT INDEX function_search IF NOT EXISTS
        FOR (n:Function)
        ON EACH [n.name, n.docstring, n.qualifiedName]
        """,
        # Classes: name, docstring
        """
        CREATE FULLTEXT INDEX class_search IF NOT EXISTS
        FOR (n:Class)
        ON EACH [n.name, n.docstring, n.qualifiedName]
        """,
        # Files: path, docstring (module-level docstring)
        """
        CREATE FULLTEXT INDEX file_search IF NOT EXISTS
        FOR (n:File)
        ON EACH [n.filePath, n.docstring, n.name]
        """,
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
        # Multi-tenant repo isolation indexes (REPO-391)
        "CREATE INDEX ON :File(repoId)",
        "CREATE INDEX ON :Function(repoId)",
        "CREATE INDEX ON :Class(repoId)",
        "CREATE INDEX ON :Module(repoId)",
        # Multi-tenant organization isolation indexes (REPO-600)
        # These enable filtering by tenant_id (org_id) for data isolation
        "CREATE INDEX ON :File(tenantId)",
        "CREATE INDEX ON :Function(tenantId)",
        "CREATE INDEX ON :Class(tenantId)",
        "CREATE INDEX ON :Module(tenantId)",
        "CREATE INDEX ON :Variable(tenantId)",
        "CREATE INDEX ON :Attribute(tenantId)",
        "CREATE INDEX ON :Concept(tenantId)",
        # Commit indexes for git history RAG (replaces Graphiti)
        "CREATE INDEX ON :Commit(sha)",
        "CREATE INDEX ON :Commit(shortSha)",
        "CREATE INDEX ON :Commit(authorEmail)",
        "CREATE INDEX ON :Commit(committedAt)",
        "CREATE INDEX ON :Commit(repoId)",
        "CREATE INDEX ON :Commit(tenantId)",  # REPO-600: tenant isolation for commits
        # Performance optimization indexes (Phase 2)
        "CREATE INDEX ON :Function(call_count)",
        "CREATE INDEX ON :Function(is_external)",
        "CREATE INDEX ON :Class(is_abstract)",
        "CREATE INDEX ON :File(is_external)",
    ]

    @staticmethod
    def _neo4j_vector_index_query(
        label: str,
        index_name: str,
        alias: str,
        dimensions: int,
        m: int = 12,
        ef_construction: int = 150
    ) -> str:
        """Generate Neo4j vector index creation query with HNSW tuning.

        Args:
            label: Node label (e.g., "Function")
            index_name: Index name (e.g., "function_embeddings")
            alias: Query alias (e.g., "f")
            dimensions: Vector dimensions (384 for local, 1536 for OpenAI)
            m: HNSW M parameter (neighbors per node, default: 12 for memory optimization)
            ef_construction: HNSW efConstruction (build quality, default: 150)

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
                `vector.similarity_function`: 'cosine',
                `vector.hnsw.m`: {m},
                `vector.hnsw.efConstruction`: {ef_construction}
            }}
        }}
        """

    @staticmethod
    def _falkordb_vector_index_query(
        label: str,
        alias: str,
        dimensions: int,
        m: int = 12,
        ef_construction: int = 150
    ) -> str:
        """Generate FalkorDB vector index creation query with HNSW tuning.

        Memory-optimized HNSW parameters:
        - M=12 (down from 16): ~25% memory savings per index
        - efConstruction=150 (down from 200): Faster index builds

        Args:
            label: Node label (e.g., "Function")
            alias: Query alias (e.g., "f")
            dimensions: Vector dimensions (384 for local, 1536 for OpenAI)
            m: HNSW M parameter (neighbors per node, default: 12)
            ef_construction: HNSW efConstruction (build quality, default: 150)

        Returns:
            Cypher query string
        """
        return f"""
        CREATE VECTOR INDEX FOR ({alias}:{label})
        ON ({alias}.embedding)
        OPTIONS {{dimension: {dimensions}, similarityFunction: 'cosine', M: {m}, efConstruction: {ef_construction}}}
        """

    def __init__(self, client):
        """Initialize schema manager.

        Args:
            client: Neo4j or FalkorDB client instance
        """
        self.client = client
        # Detect if we're using FalkorDB (check property first, then class name)
        self.is_falkordb = getattr(client, "is_falkordb", False) or type(client).__name__ == "FalkorDBClient"

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
        import time
        if self.is_falkordb:
            # First, get existing indexes to avoid slow CREATE INDEX on existing indexes
            # FalkorDB blocks for minutes when trying to create an existing index
            existing_indexes = set()
            try:
                result = self.client.execute_query("CALL db.indexes()")
                for row in result:
                    # Row format varies, but typically has label and properties
                    if isinstance(row, dict):
                        label = row.get("label", row.get("entityType", ""))
                        props = row.get("properties", row.get("property", []))
                        if isinstance(props, list):
                            for prop in props:
                                existing_indexes.add(f"{label}.{prop}")
                        else:
                            existing_indexes.add(f"{label}.{props}")
                print(f"Found {len(existing_indexes)} existing indexes")
            except Exception as e:
                print(f"Could not query existing indexes: {e}")

            # Parse which indexes we need to create
            # Format: CREATE INDEX ON :Label(property)
            import re
            indexes_to_create = []
            for index in self.FALKORDB_INDEXES:
                match = re.search(r':(\w+)\((\w+)\)', index)
                if match:
                    label, prop = match.groups()
                    key = f"{label}.{prop}"
                    if key in existing_indexes:
                        print(f"  Skipping existing index: {label}.{prop}")
                    else:
                        indexes_to_create.append((index, label, prop))
                else:
                    indexes_to_create.append((index, None, None))

            if not indexes_to_create:
                print("All indexes already exist, skipping creation")
                return

            print(f"Creating {len(indexes_to_create)} new FalkorDB indexes...")
            for i, (index, label, prop) in enumerate(indexes_to_create):
                start = time.time()
                try:
                    self.client.execute_query(index)
                    print(f"  [{i+1}/{len(indexes_to_create)}] Created index in {time.time()-start:.1f}s: {label}.{prop}")
                except Exception as e:
                    print(f"  [{i+1}/{len(indexes_to_create)}] Index failed in {time.time()-start:.1f}s: {e}")
            print("FalkorDB indexes done!")
            return

        for index in self.INDEXES:
            try:
                self.client.execute_query(index)
            except Exception as e:
                print(f"Warning: Could not create index: {e}")

    def create_fulltext_indexes(self) -> None:
        """Create full-text indexes for BM25 hybrid search (REPO-243).

        These indexes enable efficient keyword search that complements
        vector similarity search. Full-text search is particularly useful
        for exact matches (function names, class names, identifiers).

        Requires Neo4j (not supported on FalkorDB).
        """
        if self.is_falkordb:
            print("Skipping full-text indexes (not supported on FalkorDB)")
            return

        print("Creating full-text indexes for BM25 search...")

        for index_query in self.FULLTEXT_INDEX_DEFS:
            try:
                self.client.execute_query(index_query)
            except Exception as e:
                # Index may already exist
                print(f"Info: Could not create full-text index: {e}")

        print("Full-text indexes created!")

    def create_vector_indexes(self, dimensions: int = 1536) -> None:
        """Create vector indexes for RAG semantic search.

        Requires Neo4j 5.18+ or FalkorDB with vector support.

        Args:
            dimensions: Vector dimensions (1536 for OpenAI, 384 for local)
        """
        import time

        if self.is_falkordb:
            # Check existing vector indexes to avoid slow CREATE on existing
            existing_vector_indexes = set()
            try:
                result = self.client.execute_query("CALL db.indexes()")
                for row in result:
                    if isinstance(row, dict):
                        # Vector indexes have 'embedding' property
                        label = row.get("label", row.get("entityType", ""))
                        props = row.get("properties", row.get("property", []))
                        if isinstance(props, list) and "embedding" in props:
                            existing_vector_indexes.add(label)
                        elif props == "embedding":
                            existing_vector_indexes.add(label)
            except Exception as e:
                print(f"Could not query vector indexes: {e}")

            # Filter to only create missing vector indexes
            indexes_to_create = [
                (label, index_name, alias)
                for label, index_name, alias in self.VECTOR_INDEX_DEFS
                if label not in existing_vector_indexes
            ]

            if not indexes_to_create:
                print(f"All {len(self.VECTOR_INDEX_DEFS)} vector indexes already exist, skipping")
                return

            skipped = len(self.VECTOR_INDEX_DEFS) - len(indexes_to_create)
            if skipped > 0:
                print(f"Skipping {skipped} existing vector indexes")

            print(
                f"Creating {len(indexes_to_create)} vector indexes with {dimensions} dimensions "
                f"(HNSW M={self.HNSW_M}, efConstruction={self.HNSW_EF_CONSTRUCTION})..."
            )
            for i, (label, index_name, alias) in enumerate(indexes_to_create):
                start = time.time()
                try:
                    query = self._falkordb_vector_index_query(
                        label, alias, dimensions,
                        m=self.HNSW_M,
                        ef_construction=self.HNSW_EF_CONSTRUCTION
                    )
                    self.client.execute_query(query)
                    print(f"  [{i+1}/{len(indexes_to_create)}] Created vector index for {label} in {time.time()-start:.1f}s")
                except Exception as e:
                    print(f"  [{i+1}/{len(indexes_to_create)}] Vector index for {label} failed in {time.time()-start:.1f}s: {e}")
            return

        # Neo4j path
        print(
            f"Creating {len(self.VECTOR_INDEX_DEFS)} vector indexes with {dimensions} dimensions "
            f"(HNSW M={self.HNSW_M}, efConstruction={self.HNSW_EF_CONSTRUCTION})..."
        )
        for i, (label, index_name, alias) in enumerate(self.VECTOR_INDEX_DEFS):
            start = time.time()
            try:
                query = self._neo4j_vector_index_query(
                    label, index_name, alias, dimensions,
                    m=self.HNSW_M,
                    ef_construction=self.HNSW_EF_CONSTRUCTION
                )
                self.client.execute_query(query)
                print(f"  [{i+1}/{len(self.VECTOR_INDEX_DEFS)}] Created vector index for {label} in {time.time()-start:.1f}s")
            except Exception as e:
                # Index may already exist or vector support not enabled
                print(f"  [{i+1}/{len(self.VECTOR_INDEX_DEFS)}] Vector index for {label} failed/exists in {time.time()-start:.1f}s: {e}")

    def initialize(
        self,
        enable_vector_search: bool = False,
        vector_dimensions: int = 1536,
        enable_fulltext_search: bool = False,
        enable_multi_tenant: bool = False,
    ) -> None:
        """Initialize complete schema.

        REPO-600: Supports multi-tenant mode with composite uniqueness constraints.

        Args:
            enable_vector_search: Whether to create vector indexes for RAG (requires Neo4j 5.18+)
            vector_dimensions: Vector dimensions for embeddings (1536 for OpenAI, 384 for local)
            enable_fulltext_search: Whether to create full-text indexes for hybrid BM25 search
            enable_multi_tenant: Whether to create multi-tenant composite constraints (REPO-600)
        """
        print("Creating graph schema...")
        self.create_constraints()

        # REPO-600: Create multi-tenant constraints if enabled
        if enable_multi_tenant:
            self.create_multi_tenant_constraints()

        self.create_indexes()

        if enable_vector_search:
            self.create_vector_indexes(dimensions=vector_dimensions)

        if enable_fulltext_search:
            self.create_fulltext_indexes()

        print("Schema created successfully!")

    def create_multi_tenant_constraints(self) -> None:
        """Create multi-tenant composite uniqueness constraints.

        REPO-600: These constraints ensure uniqueness within a tenant while
        allowing same qualified names across different tenants.
        """
        print("Creating multi-tenant constraints...")

        for i, constraint in enumerate(self.MULTI_TENANT_CONSTRAINTS):
            try:
                self.client.execute_query(constraint)
                print(f"  [{i+1}/{len(self.MULTI_TENANT_CONSTRAINTS)}] Created: {constraint[:60]}...")
            except Exception as e:
                # Constraint might already exist
                error_msg = str(e).lower()
                if "already exists" in error_msg or "duplicate" in error_msg:
                    print(f"  [{i+1}/{len(self.MULTI_TENANT_CONSTRAINTS)}] Exists: {constraint[:60]}...")
                else:
                    print(f"  [{i+1}/{len(self.MULTI_TENANT_CONSTRAINTS)}] Failed: {e}")

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

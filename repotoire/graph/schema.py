"""Graph schema definition and initialization."""

from repotoire.graph.client import Neo4jClient


class GraphSchema:
    """Manages graph schema creation and constraints."""

    # Constraint definitions
    CONSTRAINTS = [
        # Uniqueness constraints
        "CREATE CONSTRAINT file_path_unique IF NOT EXISTS FOR (f:File) REQUIRE f.filePath IS UNIQUE",
        "CREATE CONSTRAINT module_qualified_name_unique IF NOT EXISTS FOR (m:Module) REQUIRE m.qualifiedName IS UNIQUE",
        "CREATE CONSTRAINT class_qualified_name_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.qualifiedName IS UNIQUE",
        "CREATE CONSTRAINT function_qualified_name_unique IF NOT EXISTS FOR (f:Function) REQUIRE f.qualifiedName IS UNIQUE",
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
    ]

    def __init__(self, client: Neo4jClient):
        """Initialize schema manager.

        Args:
            client: Neo4j client instance
        """
        self.client = client

    def create_constraints(self) -> None:
        """Create all uniqueness constraints."""
        for constraint in self.CONSTRAINTS:
            try:
                self.client.execute_query(constraint)
            except Exception as e:
                print(f"Warning: Could not create constraint: {e}")

    def create_indexes(self) -> None:
        """Create all indexes."""
        for index in self.INDEXES:
            try:
                self.client.execute_query(index)
            except Exception as e:
                print(f"Warning: Could not create index: {e}")

    def initialize(self) -> None:
        """Initialize complete schema."""
        print("Creating graph schema...")
        self.create_constraints()
        self.create_indexes()
        print("Schema created successfully!")

    def drop_all(self) -> None:
        """Drop all constraints and indexes. Use with caution!"""
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

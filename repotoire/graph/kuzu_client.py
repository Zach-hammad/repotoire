"""Kùzu embedded graph database client.

Provides a lightweight, embedded graph database for local-first analysis.
No Docker or external server required - just `pip install kuzu`.

Key differences from FalkorDB:
- Requires explicit schema (CREATE NODE TABLE / CREATE REL TABLE)
- Disk-based storage (low RAM usage)
- No server process (runs in-process)
- Cypher queries work with minor adaptations
"""

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from repotoire.graph.base import DatabaseClient
from repotoire.models import Entity, NodeType, Relationship, RelationshipType

logger = logging.getLogger(__name__)

# Try to import kuzu
try:
    import kuzu
    _HAS_KUZU = True
except ImportError:
    _HAS_KUZU = False
    kuzu = None


# Map our NodeTypes to Kuzu table names
NODE_TYPE_TO_TABLE = {
    NodeType.FILE: "File",
    NodeType.CLASS: "Class",
    NodeType.FUNCTION: "Function",
    NodeType.MODULE: "Module",
    NodeType.VARIABLE: "Variable",
    NodeType.ATTRIBUTE: "Variable",
    NodeType.IMPORT: "Module",
    NodeType.CONCEPT: "Concept",
    NodeType.EXTERNAL_FUNCTION: "ExternalFunction",
    NodeType.EXTERNAL_CLASS: "ExternalClass",
    NodeType.BUILTIN_FUNCTION: "BuiltinFunction",
}

# Map our RelationshipTypes to Kuzu rel table names
REL_TYPE_TO_TABLE = {
    RelationshipType.CALLS: "CALLS",
    RelationshipType.CALLS_EXTERNAL: "CALLS",  # Converted to CALLS before storage
    RelationshipType.IMPORTS: "IMPORTS",
    RelationshipType.INHERITS: "INHERITS",
    RelationshipType.CONTAINS: "CONTAINS",
    RelationshipType.DEFINES: "DEFINES",
    RelationshipType.USES: "USES",
    RelationshipType.OVERRIDES: "OVERRIDES",
    RelationshipType.DECORATES: "DECORATES",
}


class KuzuClient(DatabaseClient):
    """Embedded graph database client using Kùzu.
    
    Kùzu is an embedded graph database that supports Cypher queries.
    It runs in-process with no external dependencies.
    
    Example:
        client = KuzuClient(db_path=".repotoire/graph")
        client.execute_query("MATCH (n:Function) RETURN n.name")
    """

    @property
    def is_falkordb(self) -> bool:
        """Kuzu is not FalkorDB."""
        return False

    @property
    def is_kuzu(self) -> bool:
        """This is a Kuzu client."""
        return True

    def __init__(
        self,
        db_path: str = ".repotoire/kuzu_db",
        read_only: bool = False,
        buffer_pool_size: int = 1024 * 1024 * 1024,  # 1GB default
        max_num_threads: int = 0,  # 0 = auto-detect
    ):
        """Initialize Kùzu client.
        
        Args:
            db_path: Path to database directory (created if doesn't exist)
            read_only: Open in read-only mode
            buffer_pool_size: Buffer pool size in bytes (default 256MB)
            max_num_threads: Max threads for query execution (0=auto)
        """
        if not _HAS_KUZU:
            raise ImportError(
                "Kùzu is not installed. Install with: pip install kuzu"
            )

        self.db_path = Path(db_path)
        self.read_only = read_only

        # Ensure parent directory exists (Kuzu creates the db directory itself)
        if not read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create database
        self._db = kuzu.Database(
            str(self.db_path),
            read_only=read_only,
            buffer_pool_size=buffer_pool_size,
            max_num_threads=max_num_threads,
        )
        self._conn = kuzu.Connection(self._db)
        self._query_lock = threading.RLock()  # Thread safety for concurrent queries

        # Initialize schema if not read-only
        if not read_only:
            self._init_schema()

        logger.info(f"Kùzu database opened at {self.db_path}")

    def _init_schema(self) -> None:
        """Create node and relationship tables if they don't exist.
        
        Property names use camelCase to match existing Cypher queries.
        Relationship tables use REL TABLE GROUP for polymorphic relationships.
        """
        # Node tables with properties matching existing query expectations
        node_schemas = {
            "File": """
                CREATE NODE TABLE IF NOT EXISTS File(
                    qualifiedName STRING,
                    name STRING,
                    filePath STRING,
                    language STRING,
                    loc INT64,
                    hash STRING,
                    repoId STRING,
                    churn INT64,
                    churnCount INT64,
                    complexity DOUBLE,
                    codeHealth DOUBLE,
                    lineCount INT64,
                    is_test BOOLEAN,
                    docstring STRING,
                    semantic_context STRING,
                    embedding DOUBLE[],
                    PRIMARY KEY(qualifiedName)
                )
            """,
            "Class": """
                CREATE NODE TABLE IF NOT EXISTS Class(
                    qualifiedName STRING,
                    name STRING,
                    filePath STRING,
                    lineStart INT64,
                    lineEnd INT64,
                    complexity INT64,
                    loc INT64,
                    is_abstract BOOLEAN,
                    nesting_level INT64,
                    decorators STRING[],
                    churn INT64,
                    num_authors INT64,
                    repoId STRING,
                    docstring STRING,
                    semantic_context STRING,
                    embedding DOUBLE[],
                    PRIMARY KEY(qualifiedName)
                )
            """,
            "Function": """
                CREATE NODE TABLE IF NOT EXISTS Function(
                    qualifiedName STRING,
                    name STRING,
                    filePath STRING,
                    lineStart INT64,
                    lineEnd INT64,
                    complexity INT64,
                    loc INT64,
                    is_async BOOLEAN,
                    is_method BOOLEAN,
                    has_yield BOOLEAN,
                    yield_count INT64,
                    max_chain_depth INT64,
                    chain_example STRING,
                    parameters STRING[],
                    parameter_types STRING,
                    return_type STRING,
                    decorators STRING[],
                    in_degree INT64,
                    out_degree INT64,
                    churn INT64,
                    num_authors INT64,
                    repoId STRING,
                    docstring STRING,
                    semantic_context STRING,
                    embedding DOUBLE[],
                    PRIMARY KEY(qualifiedName)
                )
            """,
            "Module": """
                CREATE NODE TABLE IF NOT EXISTS Module(
                    qualifiedName STRING,
                    name STRING,
                    is_external BOOLEAN,
                    package STRING,
                    repoId STRING,
                    PRIMARY KEY(qualifiedName)
                )
            """,
            "Variable": """
                CREATE NODE TABLE IF NOT EXISTS Variable(
                    qualifiedName STRING,
                    name STRING,
                    filePath STRING,
                    lineStart INT64,
                    var_type STRING,
                    repoId STRING,
                    PRIMARY KEY(qualifiedName)
                )
            """,
            "DetectorMetadata": """
                CREATE NODE TABLE IF NOT EXISTS DetectorMetadata(
                    qualifiedName STRING,
                    detector STRING,
                    metric_name STRING,
                    metric_value DOUBLE,
                    repoId STRING,
                    PRIMARY KEY(qualifiedName)
                )
            """,
            "Concept": """
                CREATE NODE TABLE IF NOT EXISTS Concept(
                    qualifiedName STRING,
                    name STRING,
                    repoId STRING,
                    PRIMARY KEY(qualifiedName)
                )
            """,
            "ExternalClass": """
                CREATE NODE TABLE IF NOT EXISTS ExternalClass(
                    qualifiedName STRING,
                    name STRING,
                    module STRING,
                    repoId STRING,
                    PRIMARY KEY(qualifiedName)
                )
            """,
            "ExternalFunction": """
                CREATE NODE TABLE IF NOT EXISTS ExternalFunction(
                    qualifiedName STRING,
                    name STRING,
                    module STRING,
                    repoId STRING,
                    PRIMARY KEY(qualifiedName)
                )
            """,
            "BuiltinFunction": """
                CREATE NODE TABLE IF NOT EXISTS BuiltinFunction(
                    qualifiedName STRING,
                    name STRING,
                    module STRING,
                    repoId STRING,
                    PRIMARY KEY(qualifiedName)
                )
            """,
        }

        for table_name, schema in node_schemas.items():
            try:
                self._conn.execute(schema)
                logger.debug(f"Created/verified node table: {table_name}")
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.warning(f"Failed to create node table {table_name}: {e}")

        # Create REL TABLE GROUPs for polymorphic relationships
        # This allows queries to use :CONTAINS without specifying exact types
        rel_table_groups = [
            # CONTAINS: File->Class, File->Function, Class->Function
            """CREATE REL TABLE GROUP IF NOT EXISTS CONTAINS(
                FROM File TO Class,
                FROM File TO Function,
                FROM Class TO Function
            )""",
            # CALLS: Function->Function, Function->Class (with properties)
            # Note: REL TABLE GROUP doesn't support properties, using individual tables
            """CREATE REL TABLE IF NOT EXISTS CALLS(
                FROM Function TO Function,
                line INT64,
                call_name STRING,
                is_self_call BOOLEAN
            )""",
            """CREATE REL TABLE IF NOT EXISTS CALLS_CLASS(
                FROM Function TO Class,
                line INT64,
                call_name STRING,
                is_self_call BOOLEAN
            )""",
            # USES: Function->Variable, Function->Function
            """CREATE REL TABLE GROUP IF NOT EXISTS USES(
                FROM Function TO Variable,
                FROM Function TO Function,
                FROM Function TO Class
            )""",
            # FLAGGED_BY: Function->DetectorMetadata, Class->DetectorMetadata
            """CREATE REL TABLE GROUP IF NOT EXISTS FLAGGED_BY(
                FROM Function TO DetectorMetadata,
                FROM Class TO DetectorMetadata
            )""",
        ]

        for schema in rel_table_groups:
            try:
                self._conn.execute(schema)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.debug(f"Rel table group note: {e}")

        # Individual relationship tables for specific patterns
        rel_schemas = [
            # Imports - various targets
            "CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM File TO Module)",
            "CREATE REL TABLE IF NOT EXISTS IMPORTS_FILE(FROM File TO File)",
            "CREATE REL TABLE IF NOT EXISTS IMPORTS_EXT_CLASS(FROM File TO ExternalClass)",
            "CREATE REL TABLE IF NOT EXISTS IMPORTS_EXT_FUNC(FROM File TO ExternalFunction)",

            # Inheritance
            "CREATE REL TABLE IF NOT EXISTS INHERITS(FROM Class TO Class)",

            # Defines
            "CREATE REL TABLE IF NOT EXISTS DEFINES(FROM Class TO Function)",
            "CREATE REL TABLE IF NOT EXISTS DEFINES_VAR(FROM Function TO Variable)",

            # Overrides
            "CREATE REL TABLE IF NOT EXISTS OVERRIDES(FROM Function TO Function)",

            # Decorates
            "CREATE REL TABLE IF NOT EXISTS DECORATES(FROM Function TO Function)",

            # Tests
            "CREATE REL TABLE IF NOT EXISTS TESTS(FROM Function TO Function)",

            # Calls to external entities (with properties)
            """CREATE REL TABLE IF NOT EXISTS CALLS_EXT_FUNC(
                FROM Function TO ExternalFunction,
                line INT64,
                call_name STRING,
                is_self_call BOOLEAN
            )""",
            """CREATE REL TABLE IF NOT EXISTS CALLS_EXT_CLASS(
                FROM Function TO ExternalClass,
                line INT64,
                call_name STRING,
                is_self_call BOOLEAN
            )""",
            """CREATE REL TABLE IF NOT EXISTS CALLS_BUILTIN(
                FROM Function TO BuiltinFunction,
                line INT64,
                call_name STRING,
                is_self_call BOOLEAN
            )""",
        ]

        for schema in rel_schemas:
            try:
                self._conn.execute(schema)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    logger.debug(f"Rel table creation note: {e}")

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn = None
        if self._db:
            self._db = None
        logger.debug("Kùzu connection closed")

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> List[Dict]:
        """Execute a Cypher query and return results.
        
        Args:
            query: Cypher query string
            parameters: Query parameters (Kuzu uses $param syntax)
            timeout: Query timeout (not directly supported by Kuzu)
            
        Returns:
            List of result records as dictionaries
        """
        # Adapt query for Kuzu compatibility
        adapted_query = self._adapt_query(query)

        with self._query_lock:
            try:
                if parameters:
                    result = self._conn.execute(adapted_query, parameters)
                else:
                    result = self._conn.execute(adapted_query)

                # Convert to list of dicts
                records = []
                column_names = result.get_column_names()

                while result.has_next():
                    row = result.get_next()
                    record = dict(zip(column_names, row))
                    records.append(record)

                return records

            except Exception as e:
                # Binder exceptions are expected for missing properties (schema mismatches)
                # Log at debug level to reduce noise - callers handle gracefully
                error_str = str(e)
                if "Binder exception" in error_str or "Cannot find property" in error_str:
                    logger.debug(f"Kuzu query schema mismatch: {e}")
                else:
                    logger.error(f"Kuzu query failed: {e}\nQuery: {adapted_query}")
                raise

    def _adapt_query(self, query: str) -> str:
        """Adapt FalkorDB/Neo4j Cypher to Kuzu Cypher.
        
        Uses KuzuQueryAdapter to:
        - Transform functions (toFloat → CAST, elementId → id)
        - Check for unsupported features
        - Remove comments
        """
        from repotoire.graph.kuzu_adapter import KuzuQueryAdapter

        adapter = KuzuQueryAdapter()
        adapted, error = adapter.adapt(query)

        if error:
            raise RuntimeError(f"Kuzu compatibility: {error}")

        return adapted

    def execute_query_safe(
        self,
        query: str,
        parameters: Optional[Dict] = None,
        default: Optional[List[Dict]] = None,
    ) -> List[Dict]:
        """Execute query with graceful fallback for unsupported features.
        
        Returns default (empty list) if query uses unsupported Kuzu features.
        """
        try:
            return self.execute_query(query, parameters)
        except RuntimeError as e:
            if "Kuzu compatibility" in str(e) or "Binder exception" in str(e):
                logger.warning(f"Query not supported in Kuzu: {e}")
                return default if default is not None else []
            raise

    def create_node(self, entity: Entity) -> str:
        """Create a node in the graph."""
        table = NODE_TYPE_TO_TABLE.get(entity.node_type, "Function")

        # External* and Builtin* nodes have minimal schema
        is_external = entity.node_type in (NodeType.EXTERNAL_CLASS, NodeType.EXTERNAL_FUNCTION, NodeType.BUILTIN_FUNCTION)

        # Build properties dict with camelCase names to match schema
        props = {
            "qualifiedName": entity.qualified_name,
            "name": entity.name,
        }

        # External entities don't have filePath, lineStart, lineEnd
        if not is_external:
            props["filePath"] = entity.file_path
            # lineStart/lineEnd not in File schema
            if entity.node_type != NodeType.FILE:
                props["lineStart"] = entity.line_start
                props["lineEnd"] = entity.line_end
        else:
            # External entities have module property
            props["module"] = entity.metadata.get("module", "")

        # Add type-specific properties
        if hasattr(entity, 'complexity') and entity.complexity is not None:
            props["complexity"] = entity.complexity
        if hasattr(entity, 'is_async') and entity.is_async is not None:
            props["is_async"] = entity.is_async
        if hasattr(entity, 'is_abstract') and entity.is_abstract is not None:
            props["is_abstract"] = entity.is_abstract
        if hasattr(entity, 'language') and entity.language is not None:
            props["language"] = entity.language
        if hasattr(entity, 'loc') and entity.loc is not None:
            props["loc"] = entity.loc
        if hasattr(entity, 'is_method') and entity.is_method is not None:
            props["is_method"] = entity.is_method
        if hasattr(entity, 'parameters') and entity.parameters:
            props["parameters"] = entity.parameters
        if hasattr(entity, 'return_type') and entity.return_type:
            props["return_type"] = entity.return_type
        if hasattr(entity, 'decorators') and entity.decorators:
            props["decorators"] = entity.decorators
        if hasattr(entity, 'has_yield') and entity.has_yield is not None:
            props["has_yield"] = entity.has_yield
        if hasattr(entity, 'docstring') and entity.docstring:
            props["docstring"] = entity.docstring

        # Filter out None values (Kuzu doesn't like explicit NULLs in CREATE)
        props = {k: v for k, v in props.items() if v is not None}

        # Build CREATE query
        prop_str = ", ".join(f"{k}: ${k}" for k in props.keys())
        query = f"CREATE (n:{table} {{{prop_str}}})"

        with self._query_lock:
            self._conn.execute(query, props)
        return entity.qualified_name

    def create_relationship(self, rel: Relationship, src_type: Optional[str] = None, dst_type: Optional[str] = None) -> None:
        """Create a relationship between nodes.
        
        Args:
            rel: Relationship to create
            src_type: Source node table name (optional, will be looked up if not provided)
            dst_type: Destination node table name (optional, will be looked up if not provided)
        """
        rel_type = REL_TYPE_TO_TABLE.get(rel.rel_type, "CALLS")

        # If types not provided, look them up (and resolve actual qualified names)
        src_qname = rel.source_id
        dst_qname = rel.target_id
        
        if not src_type:
            src_type, resolved_src = self._find_node_type_and_qname(rel.source_id)
            if resolved_src:
                src_qname = resolved_src
        if not dst_type:
            dst_type, resolved_dst = self._find_node_type_and_qname(rel.target_id)
            if resolved_dst:
                dst_qname = resolved_dst

        if not src_type or not dst_type:
            logger.debug(f"Could not find node types for relationship {rel.source_id} -> {rel.target_id}")
            return

        # Get specific relationship table for this type combination
        specific_rel = self._get_specific_rel_table(rel_type, src_type, dst_type)
        final_rel = specific_rel if specific_rel else rel_type

        # Build relationship properties for CALLS
        rel_props = {}
        params = {"src": src_qname, "dst": dst_qname}

        if rel_type == "CALLS" and hasattr(rel, 'properties') and rel.properties:
            # Add CALLS-specific properties
            if 'line' in rel.properties:
                rel_props['line'] = rel.properties['line']
                params['line'] = rel.properties['line']
            if 'call_name' in rel.properties:
                rel_props['call_name'] = rel.properties['call_name']
                params['call_name'] = rel.properties['call_name']
            if 'is_self_call' in rel.properties:
                rel_props['is_self_call'] = rel.properties['is_self_call']
                params['is_self_call'] = rel.properties['is_self_call']

        # Build query with explicit labels and properties
        if rel_props:
            prop_str = ", ".join(f"{k}: ${k}" for k in rel_props.keys())
            query = f"""
            MATCH (a:{src_type} {{qualifiedName: $src}}), (b:{dst_type} {{qualifiedName: $dst}})
            CREATE (a)-[:{final_rel} {{{prop_str}}}]->(b)
            """
        else:
            query = f"""
            MATCH (a:{src_type} {{qualifiedName: $src}}), (b:{dst_type} {{qualifiedName: $dst}})
            CREATE (a)-[:{final_rel}]->(b)
            """

        with self._query_lock:
            self._conn.execute(query, params)

    def _find_node_type_and_qname(self, qualified_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Find which table a node belongs to and its actual qualified name.
        
        Returns:
            Tuple of (table_name, actual_qualified_name) or (None, None) if not found
        """
        tables = ["File", "Function", "Class", "Module", "Variable",
                  "ExternalClass", "ExternalFunction", "BuiltinFunction"]
        
        # First try exact match by qualifiedName
        for table in tables:
            try:
                with self._query_lock:
                    result = self._conn.execute(
                        f"MATCH (n:{table} {{qualifiedName: $qn}}) RETURN n.qualifiedName",
                        {"qn": qualified_name}
                    )
                    if result.has_next():
                        return table, qualified_name
            except Exception as e:
                logger.debug(f"Node type lookup failed for table {table}: {e}")
                continue
        
        # If no exact match, try matching by name (for unqualified references like base classes)
        # Only search Class and ExternalClass for inheritance targets
        if '::' not in qualified_name and '/' not in qualified_name:
            for table in ["Class", "ExternalClass"]:
                try:
                    with self._query_lock:
                        result = self._conn.execute(
                            f"MATCH (n:{table}) WHERE n.name = $name RETURN n.qualifiedName LIMIT 1",
                            {"name": qualified_name}
                        )
                        if result.has_next():
                            actual_qname = result.get_next()[0]
                            return table, actual_qname
                except Exception as e:
                    logger.debug(f"Name lookup failed for table {table}: {e}")
                    continue
        
        return None, None
    
    def _find_node_type(self, qualified_name: str) -> Optional[str]:
        """Find which table a node belongs to."""
        node_type, _ = self._find_node_type_and_qname(qualified_name)
        return node_type

    def _get_specific_rel_table(self, base_rel: str, src_type: str, dst_type: str) -> Optional[str]:
        """Get specific relationship table for given node types."""
        # Map (base_rel, src, dst) -> specific table
        specific_tables = {
            ("IMPORTS", "File", "File"): "IMPORTS_FILE",
            ("IMPORTS", "File", "ExternalClass"): "IMPORTS_EXT_CLASS",
            ("IMPORTS", "File", "ExternalFunction"): "IMPORTS_EXT_FUNC",
            ("CALLS", "Function", "Function"): "CALLS",
            ("CALLS", "Function", "Class"): "CALLS_CLASS",
            ("CALLS", "Function", "ExternalFunction"): "CALLS_EXT_FUNC",
            ("CALLS", "Function", "ExternalClass"): "CALLS_EXT_CLASS",
            ("CALLS", "Function", "BuiltinFunction"): "CALLS_BUILTIN",
        }
        return specific_tables.get((base_rel, src_type, dst_type))

    def batch_create_nodes(self, entities: List[Entity]) -> Dict[str, str]:
        """Create multiple nodes efficiently."""
        result_map = {}

        # Group by node type for batch inserts
        by_type: Dict[NodeType, List[Entity]] = {}
        for entity in entities:
            by_type.setdefault(entity.node_type, []).append(entity)

        for node_type, type_entities in by_type.items():
            for entity in type_entities:
                try:
                    self.create_node(entity)
                    result_map[entity.qualified_name] = entity.qualified_name
                except Exception as e:
                    logger.warning(f"Failed to create node {entity.qualified_name}: {e}")

        return result_map

    def batch_create_relationships(self, relationships: List[Relationship]) -> int:
        """Create multiple relationships."""
        created = 0
        for rel in relationships:
            try:
                self.create_relationship(rel)
                created += 1
            except Exception as e:
                logger.debug(f"Failed to create relationship: {e}")
        return created

    def clear_graph(self) -> None:
        """Delete all nodes and relationships."""
        # Get all node tables and clear them
        tables = ["Function", "Class", "File", "Module", "Variable", "DetectorMetadata"]
        for table in tables:
            try:
                with self._query_lock:
                    self._conn.execute(f"MATCH (n:{table}) DELETE n")
            except Exception as e:
                logger.debug(f"Failed to clear table {table} (may not exist): {e}")

    def delete_repository(self, repo_id: str) -> int:
        """Delete all nodes for a specific repository."""
        tables = ["Function", "Class", "File", "Module", "Variable", "DetectorMetadata"]
        total_deleted = 0

        for table in tables:
            try:
                with self._query_lock:
                    result = self._conn.execute(
                        f"MATCH (n:{table}) WHERE n.repoId = $repo_id DELETE n RETURN count(*) AS deleted",
                        {"repo_id": repo_id}
                    )
                    if result.has_next():
                        row = result.get_next()
                        total_deleted += row[0] if row[0] else 0
            except Exception as e:
                logger.debug(f"Failed to delete {table} nodes for repo {repo_id}: {e}")

        return total_deleted

    def create_indexes(self) -> None:
        """Create indexes - Kuzu uses PRIMARY KEY instead of separate indexes."""
        # Kuzu already has indexes via PRIMARY KEY in schema
        # No additional indexes needed
        pass

    def get_stats(self) -> Dict[str, int]:
        """Get graph statistics."""
        stats = {}
        # Map table names to expected keys
        table_key_map = {
            "Function": "total_functions",
            "Class": "total_classes",
            "File": "total_files",
            "Module": "total_modules",
            "Variable": "total_variables",
        }

        for table, key in table_key_map.items():
            try:
                with self._query_lock:
                    result = self._conn.execute(f"MATCH (n:{table}) RETURN count(*) AS cnt")
                    if result.has_next():
                        stats[key] = result.get_next()[0]
                        # Also add legacy key for backward compatibility
                        stats[table.lower() + "_count"] = stats[key]
            except Exception as e:
                logger.debug(f"Failed to get count for table {table}: {e}")
                stats[key] = 0
                stats[table.lower() + "_count"] = 0

        return stats

    def get_all_file_paths(self) -> List[str]:
        """Get all file paths currently in the graph."""
        try:
            with self._query_lock:
                result = self._conn.execute(
                    "MATCH (f:File) RETURN f.filePath AS path"
                )
                paths = []
                while result.has_next():
                    row = result.get_next()
                    if row[0]:
                        paths.append(row[0])
                return paths
        except Exception as e:
            logger.warning(f"Failed to get file paths: {e}")
            return []

    def get_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get file metadata for incremental ingestion."""
        try:
            with self._query_lock:
                result = self._conn.execute(
                    "MATCH (f:File {filePath: $path}) RETURN f.hash AS hash, f.loc AS loc",
                    {"path": file_path}
                )
                if result.has_next():
                    row = result.get_next()
                    return {"hash": row[0], "loc": row[1]}
                return None
        except Exception as e:
            logger.debug(f"Failed to get file metadata for {file_path}: {e}")
            return None

    def batch_get_file_metadata(self, file_paths: list) -> Dict[str, Dict[str, Any]]:
        """Get file metadata for multiple files in a single query.
        
        Args:
            file_paths: List of file paths to fetch metadata for
            
        Returns:
            Dict mapping file_path to metadata dict (hash, loc).
            Files not found in database are not included in result.
        """
        if not file_paths:
            return {}

        # Kuzu supports UNWIND for batch operations
        try:
            with self._query_lock:
                result = self._conn.execute(
                    "UNWIND $paths AS path MATCH (f:File {filePath: path}) RETURN f.filePath AS filePath, f.hash AS hash, f.loc AS loc",
                    {"paths": file_paths}
                )
                metadata = {}
                while result.has_next():
                    row = result.get_next()
                    file_path = row[0]
                    if file_path:
                        metadata[file_path] = {
                            "hash": row[1],
                            "loc": row[2]
                        }
                return metadata
        except Exception as e:
            # Fall back to single-file queries
            logger.debug(f"Batch metadata query failed, falling back to single queries: {e}")
            metadata = {}
            for path in file_paths:
                meta = self.get_file_metadata(path)
                if meta:
                    metadata[path] = meta
            return metadata

    def delete_file_entities(self, file_path: str) -> int:
        """Delete a file and all its related entities."""
        deleted = 0

        # Delete functions in file
        try:
            with self._query_lock:
                result = self._conn.execute(
                    "MATCH (n:Function {filePath: $path}) DELETE n RETURN count(*) AS cnt",
                    {"path": file_path}
                )
                if result.has_next():
                    deleted += result.get_next()[0] or 0
        except Exception as e:
            logger.debug(f"Failed to delete functions in {file_path}: {e}")

        # Delete classes in file
        try:
            with self._query_lock:
                result = self._conn.execute(
                    "MATCH (n:Class {filePath: $path}) DELETE n RETURN count(*) AS cnt",
                    {"path": file_path}
                )
                if result.has_next():
                    deleted += result.get_next()[0] or 0
        except Exception as e:
            logger.debug(f"Failed to delete classes in {file_path}: {e}")

        # Delete file itself
        try:
            with self._query_lock:
                result = self._conn.execute(
                    "MATCH (f:File {filePath: $path}) DELETE f RETURN count(*) AS cnt",
                    {"path": file_path}
                )
                if result.has_next():
                    deleted += result.get_next()[0] or 0
        except Exception as e:
            logger.debug(f"Failed to delete file node {file_path}: {e}")

        return deleted

    def batch_delete_file_entities(self, file_paths: list) -> int:
        """Delete multiple files and their related entities.
        
        Args:
            file_paths: List of file paths to delete
            
        Returns:
            Total count of deleted nodes
        """
        if not file_paths:
            return 0

        total_deleted = 0
        for path in file_paths:
            total_deleted += self.delete_file_entities(path)

        return total_deleted


def create_kuzu_client(
    db_path: Optional[str] = None,
    repository_path: Optional[str] = None,
) -> KuzuClient:
    """Create a Kùzu client for the given repository.
    
    Args:
        db_path: Explicit database path (overrides auto-detection)
        repository_path: Repository path (used to auto-detect db_path)
        
    Returns:
        KuzuClient instance
    """
    if db_path is None:
        if repository_path:
            db_path = str(Path(repository_path) / ".repotoire" / "kuzu_db")
        else:
            db_path = ".repotoire/kuzu_db"

    return KuzuClient(db_path=db_path)

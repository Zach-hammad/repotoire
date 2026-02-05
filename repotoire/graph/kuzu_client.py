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
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from repotoire.graph.base import DatabaseClient
from repotoire.models import Entity, Relationship, NodeType, RelationshipType

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
    NodeType.EXTERNAL_FUNCTION: "Function",
    NodeType.EXTERNAL_CLASS: "Class",
    NodeType.BUILTIN_FUNCTION: "Function",
}

# Map our RelationshipTypes to Kuzu rel table names
REL_TYPE_TO_TABLE = {
    RelationshipType.CALLS: "CALLS",
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
        buffer_pool_size: int = 256 * 1024 * 1024,  # 256MB default
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
                    churnCount INT64,
                    complexity DOUBLE,
                    codeHealth DOUBLE,
                    lineCount INT64,
                    is_test BOOLEAN,
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
                    repoId STRING,
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
                    repoId STRING,
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
            # CALLS: Function->Function, Function->Class
            """CREATE REL TABLE GROUP IF NOT EXISTS CALLS(
                FROM Function TO Function,
                FROM Function TO Class
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
            # Imports
            "CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM File TO Module)",
            "CREATE REL TABLE IF NOT EXISTS IMPORTS_FILE(FROM File TO File)",
            
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
        
        # Build properties dict with camelCase names to match schema
        props = {
            "qualifiedName": entity.qualified_name,
            "name": entity.name,
            "filePath": entity.file_path,
            "lineStart": entity.line_start,
            "lineEnd": entity.line_end,
        }
        
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
        
        # Filter out None values (Kuzu doesn't like explicit NULLs in CREATE)
        props = {k: v for k, v in props.items() if v is not None}
        
        # Build CREATE query
        prop_str = ", ".join(f"{k}: ${k}" for k in props.keys())
        query = f"CREATE (n:{table} {{{prop_str}}})"
        
        self._conn.execute(query, props)
        return entity.qualified_name

    def create_relationship(self, rel: Relationship) -> None:
        """Create a relationship between nodes."""
        # Kuzu requires knowing the node types for relationships
        # This is a simplified version - production would need type lookup
        rel_type = REL_TYPE_TO_TABLE.get(rel.rel_type, "CALLS")
        
        query = f"""
        MATCH (a {{qualifiedName: $src}}), (b {{qualifiedName: $dst}})
        CREATE (a)-[:{rel_type}]->(b)
        """
        self._conn.execute(query, {"src": rel.source, "dst": rel.target})

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
                self._conn.execute(f"MATCH (n:{table}) DELETE n")
            except Exception:
                pass  # Table might not exist

    def delete_repository(self, repo_id: str) -> int:
        """Delete all nodes for a specific repository."""
        tables = ["Function", "Class", "File", "Module", "Variable", "DetectorMetadata"]
        total_deleted = 0
        
        for table in tables:
            try:
                result = self._conn.execute(
                    f"MATCH (n:{table}) WHERE n.repoId = $repo_id DELETE n RETURN count(*) AS deleted",
                    {"repo_id": repo_id}
                )
                if result.has_next():
                    row = result.get_next()
                    total_deleted += row[0] if row[0] else 0
            except Exception:
                pass
        
        return total_deleted

    def create_indexes(self) -> None:
        """Create indexes - Kuzu uses PRIMARY KEY instead of separate indexes."""
        # Kuzu already has indexes via PRIMARY KEY in schema
        # No additional indexes needed
        pass

    def get_stats(self) -> Dict[str, int]:
        """Get graph statistics."""
        stats = {}
        tables = ["Function", "Class", "File", "Module", "Variable"]
        
        for table in tables:
            try:
                result = self._conn.execute(f"MATCH (n:{table}) RETURN count(*) AS cnt")
                if result.has_next():
                    stats[table.lower() + "_count"] = result.get_next()[0]
            except Exception:
                stats[table.lower() + "_count"] = 0
        
        return stats

    def get_all_file_paths(self) -> List[str]:
        """Get all file paths currently in the graph."""
        try:
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
            result = self._conn.execute(
                "MATCH (f:File {filePath: $path}) RETURN f.hash AS hash, f.loc AS loc",
                {"path": file_path}
            )
            if result.has_next():
                row = result.get_next()
                return {"hash": row[0], "loc": row[1]}
            return None
        except Exception:
            return None

    def delete_file_entities(self, file_path: str) -> int:
        """Delete a file and all its related entities."""
        deleted = 0
        
        # Delete functions in file
        try:
            result = self._conn.execute(
                "MATCH (n:Function {filePath: $path}) DELETE n RETURN count(*) AS cnt",
                {"path": file_path}
            )
            if result.has_next():
                deleted += result.get_next()[0] or 0
        except Exception:
            pass
        
        # Delete classes in file
        try:
            result = self._conn.execute(
                "MATCH (n:Class {filePath: $path}) DELETE n RETURN count(*) AS cnt",
                {"path": file_path}
            )
            if result.has_next():
                deleted += result.get_next()[0] or 0
        except Exception:
            pass
        
        # Delete file itself
        try:
            result = self._conn.execute(
                "MATCH (f:File {filePath: $path}) DELETE f RETURN count(*) AS cnt",
                {"path": file_path}
            )
            if result.has_next():
                deleted += result.get_next()[0] or 0
        except Exception:
            pass
        
        return deleted


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

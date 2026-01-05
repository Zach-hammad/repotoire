"""FalkorDB database client.

Drop-in replacement for Neo4jClient using FalkorDB (Redis-based graph database).
"""

from typing import Any, Dict, List, Optional
import logging
import time

from repotoire.graph.base import DatabaseClient
from repotoire.models import Entity, Relationship, NodeType, RelationshipType
from repotoire.validation import validate_identifier

logger = logging.getLogger(__name__)


class FalkorDBClient(DatabaseClient):
    """Client for interacting with FalkorDB graph database.

    FalkorDB is a Redis-based graph database that supports Cypher queries.
    This client provides the same interface as Neo4jClient for compatibility.
    """

    @property
    def is_falkordb(self) -> bool:
        """Check if this is a FalkorDB client.

        Override base class to return True for FalkorDB.
        """
        return True

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        graph_name: str = "repotoire",
        password: Optional[str] = None,
        ssl: bool = False,
        socket_timeout: Optional[float] = None,
        socket_connect_timeout: Optional[float] = None,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        retry_backoff_factor: float = 2.0,
        **kwargs,  # Accept but ignore Neo4j-specific params
    ):
        """Initialize FalkorDB client.

        Args:
            host: FalkorDB host
            port: FalkorDB port (Redis protocol)
            graph_name: Name of the graph to use
            password: Optional Redis password
            ssl: Enable TLS/SSL connection (required for Fly.io external access)
            socket_timeout: Socket timeout in seconds
            socket_connect_timeout: Connection timeout in seconds
            max_retries: Maximum retry attempts
            retry_base_delay: Base delay between retries
            retry_backoff_factor: Backoff multiplier
        """
        self.host = host
        self.port = port
        self.graph_name = graph_name
        self.password = password
        self.ssl = ssl
        self.socket_timeout = socket_timeout
        self.socket_connect_timeout = socket_connect_timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_backoff_factor = retry_backoff_factor

        # Connect to FalkorDB
        self._connect()
        logger.info(f"Connected to FalkorDB at {host}:{port}, graph: {graph_name}")

    def _connect(self) -> None:
        """Establish connection to FalkorDB."""
        try:
            from falkordb import FalkorDB
        except ImportError:
            raise ImportError("falkordb package required: pip install falkordb")

        # Build connection kwargs
        conn_kwargs = {
            "host": self.host,
            "port": self.port,
            "password": self.password,
            "ssl": self.ssl,
        }
        if self.socket_timeout is not None:
            conn_kwargs["socket_timeout"] = self.socket_timeout
        if self.socket_connect_timeout is not None:
            conn_kwargs["socket_connect_timeout"] = self.socket_connect_timeout

        self.db = FalkorDB(**conn_kwargs)
        self.graph = self.db.select_graph(self.graph_name)

    def close(self) -> None:
        """Close database connection."""
        # FalkorDB uses Redis connection which auto-closes
        logger.info("Closed FalkorDB connection")

    def __enter__(self) -> "FalkorDBClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> List[Dict]:
        """Execute a Cypher query and return results.

        Args:
            query: Cypher query string
            parameters: Query parameters
            timeout: Query timeout (not directly supported by FalkorDB)

        Returns:
            List of result records as dictionaries
        """
        params = parameters or {}

        # FalkorDB uses $param syntax like Neo4j
        attempt = 0
        last_exception = None

        while attempt <= self.max_retries:
            try:
                result = self.graph.query(query, params)

                # Convert FalkorDB result to list of dicts
                if not result.result_set:
                    return []

                # Get column names from header
                header = result.header if hasattr(result, 'header') else []

                records = []
                for row in result.result_set:
                    if header:
                        record = {header[i][1]: self._convert_value(row[i])
                                  for i in range(len(row))}
                    else:
                        # Fallback if no header
                        record = {f"col_{i}": self._convert_value(v)
                                  for i, v in enumerate(row)}
                    records.append(record)

                return records

            except Exception as e:
                last_exception = e
                attempt += 1
                if attempt > self.max_retries:
                    logger.error(f"Query failed after {self.max_retries} retries: {e}")
                    logger.error(f"Failed query: {query}")  # Log full query
                    logger.error(f"Failed params: {params}")  # Log params
                    raise
                delay = self.retry_base_delay * (self.retry_backoff_factor ** (attempt - 1))
                logger.warning(f"Query attempt {attempt} failed: {e}. Retrying in {delay:.1f}s...")
                time.sleep(delay)

        raise last_exception or Exception("Query failed")

    def _convert_value(self, value: Any) -> Any:
        """Convert FalkorDB value to Python type."""
        if hasattr(value, 'properties'):
            # Node or relationship - return properties
            return dict(value.properties)
        return value

    def create_node(self, entity: Entity) -> str:
        """Create a node in the graph.

        Args:
            entity: Entity to create

        Returns:
            Node ID (qualified name used as ID in FalkorDB)
        """
        assert isinstance(entity.node_type, NodeType), "node_type must be NodeType enum"

        query = f"""
        CREATE (n:{entity.node_type.value} {{
            name: $name,
            qualifiedName: $qualified_name,
            filePath: $file_path,
            lineStart: $line_start,
            lineEnd: $line_end,
            docstring: $docstring
        }})
        RETURN n.qualifiedName as id
        """

        result = self.execute_query(
            query,
            {
                "name": entity.name,
                "qualified_name": entity.qualified_name,
                "file_path": entity.file_path,
                "line_start": entity.line_start,
                "line_end": entity.line_end,
                "docstring": entity.docstring,
            },
        )

        return result[0]["id"] if result else entity.qualified_name

    def create_relationship(self, rel: Relationship) -> None:
        """Create a relationship between nodes.

        KG-1 Fix: External nodes now get proper labels
        KG-2 Fix: Uses MERGE to prevent duplicate relationships

        Args:
            rel: Relationship to create
        """
        from repotoire.graph.external_labels import get_external_node_label

        assert isinstance(rel.rel_type, RelationshipType), "rel_type must be RelationshipType enum"

        target_name = rel.target_id.split(".")[-1] if "." in rel.target_id else rel.target_id
        # KG-1 Fix: Determine proper label for external nodes
        external_label = get_external_node_label(target_name, rel.target_id)

        # KG-1 Fix: Create external nodes with proper label
        # KG-2 Fix: Use MERGE for relationships
        query = f"""
        MATCH (source {{qualifiedName: $source_id}})
        MERGE (target:{external_label} {{qualifiedName: $target_qualified_name}})
        ON CREATE SET target.name = $target_name, target.external = true
        MERGE (source)-[r:{rel.rel_type.value}]->(target)
        """

        self.execute_query(
            query,
            {
                "source_id": rel.source_id,
                "target_qualified_name": rel.target_id,
                "target_name": target_name,
            },
        )

    def batch_create_nodes(self, entities: List[Entity]) -> Dict[str, str]:
        """Create multiple nodes using batched UNWIND queries.

        Performance fix: Uses UNWIND to batch all nodes of a type in a single query
        instead of one query per node. This reduces network round-trips from N to 1.

        Args:
            entities: List of entities to create

        Returns:
            Dict mapping qualified_name to ID
        """
        by_type: Dict[str, List[Entity]] = {}
        for entity in entities:
            type_name = entity.node_type.value
            if type_name not in by_type:
                by_type[type_name] = []
            by_type[type_name].append(entity)

        id_mapping: Dict[str, str] = {}

        for node_type, entities_of_type in by_type.items():
            valid_node_types = {nt.value for nt in NodeType}
            if node_type not in valid_node_types:
                raise ValueError(f"Invalid node type: {node_type}")

            validated_node_type = validate_identifier(node_type, "node type")

            # Build batch of node data
            nodes_data = []
            for e in entities_of_type:
                entity_dict = {
                    "name": e.name,
                    "qualifiedName": e.qualified_name,
                    "filePath": e.file_path,
                    "lineStart": e.line_start,
                    "lineEnd": e.line_end,
                    "docstring": e.docstring,
                }

                # Add repo_id and repo_slug for multi-tenant isolation
                if e.repo_id:
                    entity_dict["repoId"] = e.repo_id
                if e.repo_slug:
                    entity_dict["repoSlug"] = e.repo_slug

                # Add type-specific fields
                for attr in ["is_external", "package", "loc", "hash", "language",
                             "exports", "is_abstract", "complexity", "parameters",
                             "return_type", "is_async", "decorators", "is_method",
                             "is_static", "is_classmethod", "is_property"]:
                    if hasattr(e, attr):
                        val = getattr(e, attr)
                        if attr == "last_modified" and val:
                            entity_dict["lastModified"] = val.isoformat()
                        elif val is not None:
                            entity_dict[attr] = val

                nodes_data.append(entity_dict)

            # Use UNWIND to batch all nodes in a single query
            # File nodes use filePath as unique key, all others use qualifiedName
            if node_type == "File":
                query = f"""
                UNWIND $nodes AS node
                MERGE (n:{validated_node_type} {{filePath: node.filePath}})
                ON CREATE SET n = node
                ON MATCH SET n += node
                RETURN n.qualifiedName as qualifiedName
                """
            else:
                query = f"""
                UNWIND $nodes AS node
                MERGE (n:{validated_node_type} {{qualifiedName: node.qualifiedName}})
                ON CREATE SET n = node
                ON MATCH SET n += node
                RETURN n.qualifiedName as qualifiedName
                """

            try:
                result = self.execute_query(query, {"nodes": nodes_data})
                for row in result:
                    qn = row.get("qualifiedName")
                    if qn:
                        id_mapping[qn] = qn
            except Exception as e:
                # Fallback to individual queries if UNWIND fails
                logger.warning(f"Batch UNWIND failed, falling back to individual queries: {e}")
                for e_data in nodes_data:
                    try:
                        if node_type == "File":
                            fallback_query = f"""
                            MERGE (n:{validated_node_type} {{filePath: $filePath}})
                            ON CREATE SET n = $props
                            ON MATCH SET n += $props
                            RETURN n.qualifiedName as qualifiedName
                            """
                            params = {"filePath": e_data["filePath"], "props": e_data}
                        else:
                            fallback_query = f"""
                            MERGE (n:{validated_node_type} {{qualifiedName: $qualifiedName}})
                            ON CREATE SET n = $props
                            ON MATCH SET n += $props
                            RETURN n.qualifiedName as qualifiedName
                            """
                            params = {"qualifiedName": e_data["qualifiedName"], "props": e_data}
                        result = self.execute_query(fallback_query, params)
                        if result:
                            id_mapping[e_data["qualifiedName"]] = e_data["qualifiedName"]
                    except Exception as inner_e:
                        logger.warning(f"Failed to create node {e_data.get('qualifiedName')}: {inner_e}")

        logger.info(f"Created {len(id_mapping)} nodes")
        return id_mapping

    def batch_create_relationships(self, relationships: List[Relationship]) -> int:
        """Create multiple relationships using batched UNWIND queries.

        Performance fix: Uses UNWIND to batch all relationships of a type in a single query
        instead of one query per relationship. This reduces network round-trips from N to 1.

        KG-1 Fix: External nodes now get proper labels (BuiltinFunction, ExternalFunction, ExternalClass)
        KG-2 Fix: Uses MERGE instead of CREATE to prevent duplicate relationships
        KG-3 Fix: Internal targets (with ::) use MATCH, not MERGE with External* label

        Args:
            relationships: List of relationships to create

        Returns:
            Number of relationships created
        """
        from repotoire.graph.external_labels import get_external_node_label, is_likely_external_reference

        if not relationships:
            return 0

        # Group by relationship type first
        by_type: Dict[str, List[Relationship]] = {}
        for rel in relationships:
            assert isinstance(rel.rel_type, RelationshipType)
            rel_type = rel.rel_type.value
            if rel_type not in by_type:
                by_type[rel_type] = []
            by_type[rel_type].append(rel)

        total_created = 0

        for rel_type, rels_of_type in by_type.items():
            # KG-3 Fix: Separate internal (target has ::) from external relationships
            internal_rels: List[Dict] = []
            by_external_label: Dict[str, List[Dict]] = {
                "BuiltinFunction": [],
                "ExternalFunction": [],
                "ExternalClass": [],
            }

            for r in rels_of_type:
                # KG-3 Fix: Check if target is internal (has :: separator)
                if not is_likely_external_reference(r.target_id):
                    # Internal target - both source and target already exist
                    internal_rels.append({
                        "source_id": r.source_id,
                        "target_id": r.target_id,
                    })
                else:
                    # External target - need to MERGE the target node
                    target_name = r.target_id.split(".")[-1] if "." in r.target_id else r.target_id.split("::")[-1]
                    external_label = get_external_node_label(target_name, r.target_id)
                    by_external_label[external_label].append({
                        "source_id": r.source_id,
                        "target_id": r.target_id,
                        "target_name": target_name,
                    })

            # KG-3 Fix: Process internal relationships (MATCH both nodes)
            if internal_rels:
                query = f"""
                UNWIND $rels AS rel
                MATCH (source {{qualifiedName: rel.source_id}})
                MATCH (target {{qualifiedName: rel.target_id}})
                MERGE (source)-[r:{rel_type}]->(target)
                RETURN count(r) as created
                """
                try:
                    result = self.execute_query(query, {"rels": internal_rels})
                    if result and len(result) > 0:
                        created = result[0].get("created", 0)
                        total_created += created
                        logger.debug(f"Batch created {created} {rel_type} relationships (internal)")
                except Exception as e:
                    logger.warning(f"Batch UNWIND failed for internal {rel_type}, falling back: {e}")
                    for rel_data in internal_rels:
                        fallback_query = f"""
                        MATCH (source {{qualifiedName: $source_id}})
                        MATCH (target {{qualifiedName: $target_id}})
                        MERGE (source)-[r:{rel_type}]->(target)
                        """
                        try:
                            self.execute_query(fallback_query, rel_data)
                            total_created += 1
                        except Exception as inner_e:
                            logger.debug(f"Failed to create internal relationship: {inner_e}")

            # Process external relationships (MERGE target node with External* label)
            for external_label, rels_data in by_external_label.items():
                if not rels_data:
                    continue

                # Use UNWIND to batch all relationships of this type in a single query
                # KG-1 Fix: MERGE with specific label for external nodes
                # KG-2 Fix: Use MERGE for relationships to prevent duplicates
                query = f"""
                UNWIND $rels AS rel
                MATCH (source {{qualifiedName: rel.source_id}})
                MERGE (target:{external_label} {{qualifiedName: rel.target_id}})
                ON CREATE SET target.name = rel.target_name, target.external = true
                MERGE (source)-[r:{rel_type}]->(target)
                RETURN count(r) as created
                """

                try:
                    result = self.execute_query(query, {"rels": rels_data})
                    if result and len(result) > 0:
                        created = result[0].get("created", 0)
                        total_created += created
                        logger.debug(f"Batch created {created} {rel_type} relationships to {external_label} nodes")
                except Exception as e:
                    logger.warning(f"Batch UNWIND failed for {rel_type}->{external_label}, falling back to individual queries: {e}")
                    # Fallback to individual queries if UNWIND fails
                    for rel_data in rels_data:
                        fallback_query = f"""
                        MATCH (source {{qualifiedName: $source_id}})
                        MERGE (target:{external_label} {{qualifiedName: $target_id}})
                        ON CREATE SET target.name = $target_name, target.external = true
                        MERGE (source)-[r:{rel_type}]->(target)
                        """
                        try:
                            self.execute_query(fallback_query, rel_data)
                            total_created += 1
                        except Exception as inner_e:
                            logger.warning(f"Failed to create relationship: {inner_e}")

        logger.info(f"Created {total_created} relationships")
        return total_created

    def clear_graph(self) -> None:
        """Delete all nodes and relationships."""
        # FalkorDB: delete the graph and recreate
        try:
            self.graph.delete()
        except Exception:
            pass  # Graph might not exist
        self.graph = self.db.select_graph(self.graph_name)
        logger.warning("Cleared all nodes from graph")

    def create_indexes(self) -> None:
        """Create indexes for better query performance."""
        # FalkorDB creates indexes automatically on first query
        # But we can create explicit indexes
        indexes = [
            "CREATE INDEX ON :File(filePath)",
            "CREATE INDEX ON :Class(qualifiedName)",
            "CREATE INDEX ON :Function(qualifiedName)",
        ]

        for index_query in indexes:
            try:
                self.execute_query(index_query)
            except Exception as e:
                logger.debug(f"Index creation (may already exist): {e}")

        logger.info("Created graph indexes")

    def get_context(self, entity_id: str, depth: int = 1) -> Dict:
        """Get graph context around an entity.

        Args:
            entity_id: Node qualified name
            depth: Traversal depth

        Returns:
            Context dictionary with connected nodes
        """
        # FalkorDB doesn't have APOC, use native path query
        query = f"""
        MATCH (n {{qualifiedName: $entity_id}})
        OPTIONAL MATCH path = (n)-[*1..{depth}]-(connected)
        RETURN collect(DISTINCT connected) as nodes
        """

        result = self.execute_query(query, {"entity_id": entity_id})

        if not result:
            return {}

        return {
            "nodes": result[0].get("nodes", []),
            "relationships": [],
        }

    def get_stats(self) -> Dict[str, int]:
        """Get graph statistics.

        Returns:
            Dictionary with node/relationship counts
        """
        queries = {
            "total_nodes": "MATCH (n) RETURN count(n) as count",
            "total_files": "MATCH (f:File) RETURN count(f) as count",
            "total_classes": "MATCH (c:Class) RETURN count(c) as count",
            "total_functions": "MATCH (f:Function) RETURN count(f) as count",
            "total_relationships": "MATCH ()-[r]->() RETURN count(r) as count",
            "embeddings_count": "MATCH (n) WHERE n.embedding IS NOT NULL RETURN count(n) as count",
        }

        stats = {}
        for key, query in queries.items():
            try:
                result = self.execute_query(query)
                stats[key] = result[0]["count"] if result else 0
            except Exception:
                stats[key] = 0

        return stats

    def get_relationship_type_counts(self) -> Dict[str, int]:
        """Get counts for each relationship type."""
        query = """
        MATCH ()-[r]->()
        RETURN type(r) as rel_type, count(r) as count
        ORDER BY count DESC
        """
        result = self.execute_query(query)
        return {record["rel_type"]: record["count"] for record in result}

    def get_node_label_counts(self) -> Dict[str, int]:
        """Get counts for each node label."""
        query = """
        MATCH (n)
        RETURN labels(n)[0] as label, count(n) as count
        ORDER BY count DESC
        """
        result = self.execute_query(query)
        return {record["label"]: record["count"] for record in result if record.get("label")}

    def sample_nodes(self, label: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get sample nodes of a specific label."""
        validated_label = validate_identifier(label, "label")
        query = f"""
        MATCH (n:{validated_label})
        RETURN n
        LIMIT {int(limit)}
        """
        result = self.execute_query(query)
        return [record.get("n", {}) for record in result]

    def validate_schema_integrity(self) -> Dict[str, Any]:
        """Validate graph schema integrity."""
        issues = {}

        # Check for functions missing complexity
        query = """
        MATCH (f:Function)
        WHERE f.complexity IS NULL
        RETURN count(f) as count
        """
        try:
            result = self.execute_query(query)
            missing = result[0]["count"] if result else 0
            if missing > 0:
                issues["functions_missing_complexity"] = missing
        except Exception:
            pass

        return {
            "valid": len(issues) == 0,
            "issues": issues
        }

    def get_all_file_paths(self) -> List[str]:
        """Get all file paths currently in the graph."""
        query = """
        MATCH (f:File)
        RETURN f.filePath as filePath
        """
        result = self.execute_query(query)
        return [record["filePath"] for record in result if record.get("filePath")]

    def get_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get file metadata for incremental ingestion."""
        query = """
        MATCH (f:File {filePath: $path})
        RETURN f.hash as hash, f.lastModified as lastModified
        """
        result = self.execute_query(query, {"path": file_path})
        return result[0] if result else None

    def delete_file_entities(self, file_path: str) -> int:
        """Delete a file and all its related entities."""
        query = """
        MATCH (f:File {filePath: $path})
        OPTIONAL MATCH (f)-[:CONTAINS*]->(entity)
        DETACH DELETE f, entity
        RETURN count(f) + count(entity) as deletedCount
        """
        result = self.execute_query(query, {"path": file_path})
        deleted_count = result[0]["deletedCount"] if result else 0
        logger.info(f"Deleted {deleted_count} nodes for file: {file_path}")
        return deleted_count

    def get_pool_metrics(self) -> Dict[str, Any]:
        """Get connection metrics."""
        return {
            "host": self.host,
            "port": self.port,
            "graph_name": self.graph_name,
            "backend": "FalkorDB",
        }

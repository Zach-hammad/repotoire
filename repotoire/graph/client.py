"""Neo4j database client."""

from typing import Any, Dict, List, Optional, Callable, TypeVar
from neo4j import GraphDatabase, Driver, Result
from neo4j.exceptions import ServiceUnavailable, SessionExpired
import logging
import time

from repotoire.models import Entity, Relationship, NodeType, RelationshipType
from repotoire.validation import validate_identifier

logger = logging.getLogger(__name__)

T = TypeVar('T')


class Neo4jClient:
    """Client for interacting with Neo4j graph database."""

    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        username: str = "neo4j",
        password: str = "password",
        max_retries: int = 3,
        retry_backoff_factor: float = 2.0,
        retry_base_delay: float = 1.0,
        max_connection_pool_size: int = 50,
        connection_timeout: float = 30.0,
        max_connection_lifetime: int = 3600,
        query_timeout: float = 60.0,
        encrypted: bool = False,
    ):
        """Initialize Neo4j client.

        Args:
            uri: Neo4j connection URI
            username: Database username
            password: Database password
            max_retries: Maximum number of connection retry attempts (default: 3)
            retry_backoff_factor: Exponential backoff multiplier (default: 2.0)
            retry_base_delay: Base delay in seconds between retries (default: 1.0)
            max_connection_pool_size: Maximum number of connections in pool (default: 50)
            connection_timeout: Timeout for acquiring connection in seconds (default: 30.0)
            max_connection_lifetime: Maximum connection lifetime in seconds (default: 3600)
            query_timeout: Default query timeout in seconds (default: 60.0)
            encrypted: Whether to use encrypted connection (default: False for local dev)
        """
        self.uri = uri
        self.username = username
        self.password = password
        self.max_retries = max_retries
        self.retry_backoff_factor = retry_backoff_factor
        self.retry_base_delay = retry_base_delay
        self.max_connection_pool_size = max_connection_pool_size
        self.connection_timeout = connection_timeout
        self.max_connection_lifetime = max_connection_lifetime
        self.query_timeout = query_timeout
        self.encrypted = encrypted

        # Connect with retry logic
        self.driver: Driver = self._connect_with_retry()
        logger.info(
            f"Connected to Neo4j at {uri} "
            f"(pool_size={max_connection_pool_size}, "
            f"query_timeout={query_timeout}s, "
            f"encrypted={encrypted})"
        )

    def close(self) -> None:
        """Close database connection."""
        self.driver.close()
        logger.info("Closed Neo4j connection")

    def __enter__(self) -> "Neo4jClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _connect_with_retry(self) -> Driver:
        """Establish connection with retry logic and exponential backoff.

        Returns:
            Neo4j Driver instance

        Raises:
            ServiceUnavailable: If connection fails after max retries
        """
        attempt = 0
        last_exception = None

        while attempt <= self.max_retries:
            try:
                driver = GraphDatabase.driver(
                    self.uri,
                    auth=(self.username, self.password),
                    max_connection_pool_size=self.max_connection_pool_size,
                    connection_acquisition_timeout=self.connection_timeout,
                    max_transaction_retry_time=15.0,
                    connection_timeout=self.connection_timeout,
                    max_connection_lifetime=self.max_connection_lifetime,
                    keep_alive=True,
                    encrypted=self.encrypted,
                )
                # Verify connectivity with a simple query
                driver.verify_connectivity()
                if attempt > 0:
                    logger.info(f"Successfully connected to Neo4j at {self.uri} after {attempt} retries")
                return driver
            except (ServiceUnavailable, Exception) as e:
                last_exception = e
                attempt += 1

                if attempt > self.max_retries:
                    logger.error(
                        f"Failed to connect to Neo4j at {self.uri} after {self.max_retries} retries: {e}"
                    )
                    raise ServiceUnavailable(
                        f"Could not connect to Neo4j at {self.uri} after {self.max_retries} attempts. "
                        f"Please check that Neo4j is running and accessible. Last error: {e}"
                    ) from e

                # Calculate exponential backoff delay
                delay = self.retry_base_delay * (self.retry_backoff_factor ** (attempt - 1))
                logger.warning(
                    f"Connection attempt {attempt}/{self.max_retries} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)

        # Should never reach here, but for type safety
        raise ServiceUnavailable("Connection failed") from last_exception

    def _retry_operation(self, operation: Callable[[], T], operation_name: str = "operation") -> T:
        """Execute an operation with retry logic for transient errors.

        Args:
            operation: Function to execute
            operation_name: Human-readable name for logging

        Returns:
            Result of the operation

        Raises:
            Exception: If operation fails after max retries
        """
        attempt = 0
        last_exception = None

        while attempt <= self.max_retries:
            try:
                return operation()
            except (ServiceUnavailable, SessionExpired) as e:
                last_exception = e
                attempt += 1

                if attempt > self.max_retries:
                    logger.error(
                        f"{operation_name} failed after {self.max_retries} retries: {e}"
                    )
                    raise

                # Calculate exponential backoff delay
                delay = self.retry_base_delay * (self.retry_backoff_factor ** (attempt - 1))
                logger.warning(
                    f"{operation_name} attempt {attempt}/{self.max_retries} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                time.sleep(delay)
            except Exception as e:
                # Non-transient errors should fail immediately
                logger.error(f"{operation_name} failed with non-transient error: {e}")
                raise

        # Should never reach here, but for type safety
        raise last_exception or Exception(f"{operation_name} failed")

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict] = None,
        timeout: Optional[float] = None,
    ) -> List[Dict]:
        """Execute a Cypher query and return results with retry logic and timeout.

        Args:
            query: Cypher query string
            parameters: Query parameters
            timeout: Query timeout in seconds (uses default if not specified)

        Returns:
            List of result records as dictionaries

        Raises:
            Exception: If query times out or fails after retries
        """
        timeout_ms = int((timeout or self.query_timeout) * 1000)

        def _execute():
            with self.driver.session() as session:
                result: Result = session.run(
                    query, parameters or {}, timeout=timeout_ms
                )
                return [dict(record) for record in result]

        return self._retry_operation(_execute, operation_name="execute_query")

    def create_node(self, entity: Entity) -> str:
        """Create a node in the graph.

        Args:
            entity: Entity to create

        Returns:
            Node ID
        """
        # SECURITY: entity.node_type.value is from NodeType enum - safe for f-string
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
        RETURN elementId(n) as id
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

        return result[0]["id"]

    def create_relationship(self, rel: Relationship) -> None:
        """Create a relationship between nodes.

        Args:
            rel: Relationship to create
        """
        # SECURITY: rel.rel_type.value is from RelationshipType enum - safe for f-string
        assert isinstance(rel.rel_type, RelationshipType), "rel_type must be RelationshipType enum"

        # Try to find source by elementId, target by elementId or qualifiedName
        query = f"""
        MATCH (source)
        WHERE elementId(source) = $source_id
        MERGE (target {{qualifiedName: $target_qualified_name}})
        ON CREATE SET target.name = $target_name, target.external = true
        CREATE (source)-[r:{rel.rel_type.value}]->(target)
        SET r = $properties
        """

        # Extract target name from qualified name (e.g., "os.path" -> "path")
        target_name = rel.target_id.split(".")[-1] if "." in rel.target_id else rel.target_id

        self.execute_query(
            query,
            {
                "source_id": rel.source_id,
                "target_id": rel.target_id,
                "target_qualified_name": rel.target_id,
                "target_name": target_name,
                "properties": rel.properties,
            },
        )

    def batch_create_nodes(self, entities: List[Entity]) -> Dict[str, str]:
        """Create multiple nodes in a write transaction.

        Args:
            entities: List of entities to create

        Returns:
            Dict mapping qualified_name to elementId
        """
        # Group by type for efficient batch creation
        by_type: Dict[str, List[Entity]] = {}
        for entity in entities:
            type_name = entity.node_type.value
            if type_name not in by_type:
                by_type[type_name] = []
            by_type[type_name].append(entity)

        id_mapping: Dict[str, str] = {}

        for node_type, entities_of_type in by_type.items():
            # SECURITY: Validate node type to prevent Cypher injection
            # node_type is from NodeType enum value, but we validate it anyway
            # (assertions can be disabled with -O flag, so we use proper validation)
            valid_node_types = {nt.value for nt in NodeType}
            if node_type not in valid_node_types:
                raise ValueError(f"Invalid node type: {node_type}. Must be one of {valid_node_types}")

            # Additional validation: ensure it's a valid identifier
            validated_node_type = validate_identifier(node_type, "node type")

            # Use MERGE for Module nodes to avoid duplicates (multiple files import same module)
            # Use CREATE for other node types
            if node_type == "Module":
                query = f"""
                UNWIND $entities AS entity
                MERGE (n:{validated_node_type} {{qualifiedName: entity.qualifiedName}})
                ON CREATE SET n = entity
                ON MATCH SET n += entity
                RETURN elementId(n) as id, entity.qualifiedName as qualifiedName
                """
            else:
                query = f"""
                UNWIND $entities AS entity
                CREATE (n:{validated_node_type})
                SET n = entity
                RETURN elementId(n) as id, entity.qualifiedName as qualifiedName
                """

            # Convert entities to dicts, including all type-specific fields
            entity_dicts = []
            for e in entities_of_type:
                entity_dict = {
                    "name": e.name,
                    "qualifiedName": e.qualified_name,
                    "filePath": e.file_path,
                    "lineStart": e.line_start,
                    "lineEnd": e.line_end,
                    "docstring": e.docstring,
                }

                # Add type-specific fields
                if hasattr(e, "is_external"):  # Module
                    entity_dict["is_external"] = e.is_external
                if hasattr(e, "package"):  # Module
                    entity_dict["package"] = e.package
                if hasattr(e, "loc"):  # File
                    entity_dict["loc"] = e.loc
                if hasattr(e, "hash"):  # File
                    entity_dict["hash"] = e.hash
                if hasattr(e, "language"):  # File
                    entity_dict["language"] = e.language
                if hasattr(e, "last_modified"):  # File
                    # Convert datetime to ISO string for Neo4j
                    entity_dict["lastModified"] = e.last_modified.isoformat() if e.last_modified else None
                if hasattr(e, "exports"):  # File
                    entity_dict["exports"] = e.exports
                if hasattr(e, "is_abstract"):  # Class
                    entity_dict["is_abstract"] = e.is_abstract
                if hasattr(e, "complexity"):  # Class/Function
                    entity_dict["complexity"] = e.complexity
                if hasattr(e, "parameters"):  # Function
                    entity_dict["parameters"] = e.parameters
                if hasattr(e, "return_type"):  # Function
                    entity_dict["return_type"] = e.return_type
                if hasattr(e, "is_async"):  # Function
                    entity_dict["is_async"] = e.is_async
                if hasattr(e, "decorators"):  # Function
                    entity_dict["decorators"] = e.decorators
                if hasattr(e, "is_method"):  # Function
                    entity_dict["is_method"] = e.is_method
                if hasattr(e, "is_static"):  # Function
                    entity_dict["is_static"] = e.is_static
                if hasattr(e, "is_classmethod"):  # Function
                    entity_dict["is_classmethod"] = e.is_classmethod
                if hasattr(e, "is_property"):  # Function
                    entity_dict["is_property"] = e.is_property

                entity_dicts.append(entity_dict)

            # Use write transaction for batch node creation
            def _create_batch(tx, q: str, params: Dict):
                result = tx.run(q, params)
                return [dict(record) for record in result]

            def _execute_write():
                with self.driver.session() as session:
                    return session.execute_write(_create_batch, query, {"entities": entity_dicts})

            results = self._retry_operation(_execute_write, operation_name="batch_create_nodes")
            for r in results:
                id_mapping[r["qualifiedName"]] = r["id"]

        logger.info(f"Created {len(id_mapping)} nodes")
        return id_mapping

    def batch_create_relationships(self, relationships: List[Relationship]) -> int:
        """Create multiple relationships in a write transaction.

        Accepts relationships with source_id and target_id as qualified names.
        Will match existing nodes by qualifiedName, and create external nodes
        for targets that don't exist (e.g., external imports).

        Args:
            relationships: List of relationships to create

        Returns:
            Number of relationships created
        """
        if not relationships:
            return 0

        # Group relationships by type for efficient batch creation
        by_type: Dict[str, List[Relationship]] = {}
        for rel in relationships:
            # SECURITY: rel.rel_type.value is from RelationshipType enum
            assert isinstance(rel.rel_type, RelationshipType), "rel_type must be RelationshipType enum"
            rel_type = rel.rel_type.value
            if rel_type not in by_type:
                by_type[rel_type] = []
            by_type[rel_type].append(rel)

        total_created = 0

        for rel_type, rels_of_type in by_type.items():
            # Build list of relationship data
            rel_data = [
                {
                    "source_id": r.source_id,
                    "target_id": r.target_id,
                    "target_name": r.target_id.split(".")[-1] if "." in r.target_id else r.target_id.split("::")[-1],
                    "properties": r.properties,
                }
                for r in rels_of_type
            ]

            # SECURITY: rel_type validated above via assertion
            # Match source by qualifiedName (can be File path, Class/Function qualified name)
            # MERGE target by qualifiedName (create if external import/reference)
            query = f"""
            UNWIND $rels AS rel
            MATCH (source {{qualifiedName: rel.source_id}})
            MERGE (target {{qualifiedName: rel.target_id}})
            ON CREATE SET target.name = rel.target_name, target.external = true
            CREATE (source)-[r:{rel_type}]->(target)
            SET r = rel.properties
            """

            # Use write transaction for batch relationship creation
            def _create_batch(tx, q: str, params: Dict):
                result = tx.run(q, params)
                return result.consume().counters.relationships_created

            def _execute_write():
                with self.driver.session() as session:
                    return session.execute_write(_create_batch, query, {"rels": rel_data})

            created_count = self._retry_operation(_execute_write, operation_name="batch_create_relationships")
            total_created += len(rels_of_type)

        logger.info(f"Batch created {total_created} relationships")
        return total_created

    def clear_graph(self) -> None:
        """Delete all nodes and relationships. Use with caution!"""
        query = "MATCH (n) DETACH DELETE n"
        self.execute_query(query)
        logger.warning("Cleared all nodes from graph")

    def create_indexes(self) -> None:
        """Create indexes for better query performance."""
        indexes = [
            "CREATE INDEX file_path IF NOT EXISTS FOR (f:File) ON (f.filePath)",
            "CREATE INDEX class_name IF NOT EXISTS FOR (c:Class) ON (c.qualifiedName)",
            "CREATE INDEX function_name IF NOT EXISTS FOR (f:Function) ON (f.qualifiedName)",
        ]

        for index_query in indexes:
            self.execute_query(index_query)

        logger.info("Created graph indexes")

    def get_context(self, entity_id: str, depth: int = 1) -> Dict:
        """Get graph context around an entity.

        Args:
            entity_id: Node ID
            depth: Traversal depth

        Returns:
            Context dictionary with connected nodes
        """
        query = """
        MATCH (n)
        WHERE elementId(n) = $entity_id
        CALL apoc.path.subgraphAll(n, {
            maxLevel: $depth,
            relationshipFilter: 'CALLS>|USES>|IMPORTS>'
        })
        YIELD nodes, relationships
        RETURN nodes, relationships
        """

        result = self.execute_query(query, {"entity_id": entity_id, "depth": depth})

        if not result:
            return {}

        return {
            "nodes": [dict(node) for node in result[0].get("nodes", [])],
            "relationships": [dict(rel) for rel in result[0].get("relationships", [])],
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
            result = self.execute_query(query)
            stats[key] = result[0]["count"] if result else 0

        return stats

    def get_relationship_type_counts(self) -> Dict[str, int]:
        """Get counts for each relationship type.

        Returns:
            Dictionary mapping relationship type to count
        """
        query = """
        MATCH ()-[r]->()
        RETURN type(r) as rel_type, count(r) as count
        ORDER BY count DESC
        """
        result = self.execute_query(query)
        return {record["rel_type"]: record["count"] for record in result}

    def get_node_label_counts(self) -> Dict[str, int]:
        """Get counts for each node label.

        Returns:
            Dictionary mapping node label to count
        """
        query = """
        MATCH (n)
        RETURN labels(n)[0] as label, count(n) as count
        ORDER BY count DESC
        """
        result = self.execute_query(query)
        return {record["label"]: record["count"] for record in result if record["label"]}

    def sample_nodes(self, label: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Get sample nodes of a specific label.

        Args:
            label: Node label to sample
            limit: Maximum number of samples

        Returns:
            List of node properties
        """
        query = f"""
        MATCH (n:{label})
        RETURN properties(n) as props
        LIMIT {int(limit)}
        """
        result = self.execute_query(query)
        return [record["props"] for record in result]

    def validate_schema_integrity(self) -> Dict[str, Any]:
        """Validate graph schema integrity.

        Returns:
            Dictionary with validation results
        """
        issues = {}

        # Check for orphaned relationships
        orphan_query = """
        MATCH ()-[r]->()
        WHERE NOT exists((startNode(r))) OR NOT exists((endNode(r)))
        RETURN count(r) as count
        """
        orphan_result = self.execute_query(orphan_query)
        orphan_count = orphan_result[0]["count"] if orphan_result else 0
        if orphan_count > 0:
            issues["orphaned_relationships"] = orphan_count

        # Check for nodes missing required properties
        # Function nodes should have complexity
        missing_complexity_query = """
        MATCH (f:Function)
        WHERE f.complexity IS NULL
        RETURN count(f) as count
        """
        result = self.execute_query(missing_complexity_query)
        missing_complexity = result[0]["count"] if result else 0
        if missing_complexity > 0:
            issues["functions_missing_complexity"] = missing_complexity

        return {
            "valid": len(issues) == 0,
            "issues": issues
        }

    def get_all_file_paths(self) -> List[str]:
        """Get all file paths currently in the graph.

        Returns:
            List of file paths
        """
        query = """
        MATCH (f:File)
        RETURN f.filePath as filePath
        """
        result = self.execute_query(query)
        return [record["filePath"] for record in result]

    def get_file_metadata(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Get file metadata for incremental ingestion.

        Args:
            file_path: Path to file

        Returns:
            Dictionary with hash and lastModified, or None if file not found
        """
        query = """
        MATCH (f:File {filePath: $path})
        RETURN f.hash as hash, f.lastModified as lastModified
        """
        result = self.execute_query(query, {"path": file_path})
        return result[0] if result else None

    def delete_file_entities(self, file_path: str) -> int:
        """Delete a file and all its related entities from the graph.

        This is used during incremental ingestion to remove outdated data
        before re-ingesting a modified file.

        Args:
            file_path: Path to file to delete

        Returns:
            Number of nodes deleted
        """
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
        """Get connection pool metrics for monitoring.

        Returns:
            Dictionary with pool statistics including:
            - in_use: Number of connections currently in use
            - idle: Number of idle connections
            - max_size: Maximum pool size
            - acquisition_timeout: Connection acquisition timeout
            - max_lifetime: Maximum connection lifetime

        Example:
            >>> client = Neo4jClient()
            >>> metrics = client.get_pool_metrics()
            >>> logger.info(f"Pool: {metrics['in_use']}/{metrics['max_size']} in use")
        """
        try:
            # Get pool metrics from driver
            pool = self.driver._pool

            metrics = {
                "max_size": self.max_connection_pool_size,
                "acquisition_timeout": self.connection_timeout,
                "max_lifetime": self.max_connection_lifetime,
                "query_timeout": self.query_timeout,
                "encrypted": self.encrypted,
            }

            # Try to get current pool statistics if available
            if hasattr(pool, "in_use_connection_count"):
                metrics["in_use"] = pool.in_use_connection_count
            if hasattr(pool, "idle_count"):
                metrics["idle"] = pool.idle_count

            return metrics

        except Exception as e:
            logger.warning(f"Could not retrieve pool metrics: {e}")
            return {
                "max_size": self.max_connection_pool_size,
                "acquisition_timeout": self.connection_timeout,
                "max_lifetime": self.max_connection_lifetime,
                "query_timeout": self.query_timeout,
                "encrypted": self.encrypted,
                "error": str(e),
            }

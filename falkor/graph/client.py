"""Neo4j database client."""

from typing import Any, Dict, List, Optional, Callable, TypeVar
from neo4j import GraphDatabase, Driver, Result
from neo4j.exceptions import ServiceUnavailable, SessionExpired
import logging
import time

from falkor.models import Entity, Relationship, NodeType, RelationshipType

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
    ):
        """Initialize Neo4j client.

        Args:
            uri: Neo4j connection URI
            username: Database username
            password: Database password
            max_retries: Maximum number of connection retry attempts (default: 3)
            retry_backoff_factor: Exponential backoff multiplier (default: 2.0)
            retry_base_delay: Base delay in seconds between retries (default: 1.0)
        """
        self.uri = uri
        self.username = username
        self.password = password
        self.max_retries = max_retries
        self.retry_backoff_factor = retry_backoff_factor
        self.retry_base_delay = retry_base_delay

        # Connect with retry logic
        self.driver: Driver = self._connect_with_retry()
        logger.info(f"Connected to Neo4j at {uri}")

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
                driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password))
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

    def execute_query(self, query: str, parameters: Optional[Dict] = None) -> List[Dict]:
        """Execute a Cypher query and return results with retry logic.

        Args:
            query: Cypher query string
            parameters: Query parameters

        Returns:
            List of result records as dictionaries
        """
        def _execute():
            with self.driver.session() as session:
                result: Result = session.run(query, parameters or {})
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
        """Create multiple nodes in a single transaction.

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
            # SECURITY: node_type is from NodeType enum value - safe for f-string
            # Validate it's a valid node type string
            assert node_type in [nt.value for nt in NodeType], f"Invalid node type: {node_type}"

            # Use MERGE for Module nodes to avoid duplicates (multiple files import same module)
            # Use CREATE for other node types
            if node_type == "Module":
                query = f"""
                UNWIND $entities AS entity
                MERGE (n:{node_type} {{qualifiedName: entity.qualifiedName}})
                ON CREATE SET n = entity
                ON MATCH SET n += entity
                RETURN elementId(n) as id, entity.qualifiedName as qualifiedName
                """
            else:
                query = f"""
                UNWIND $entities AS entity
                CREATE (n:{node_type})
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

                entity_dicts.append(entity_dict)

            results = self.execute_query(query, {"entities": entity_dicts})
            for r in results:
                id_mapping[r["qualifiedName"]] = r["id"]

        logger.info(f"Created {len(id_mapping)} nodes")
        return id_mapping

    def batch_create_relationships(self, relationships: List[Relationship]) -> int:
        """Create multiple relationships in a single transaction.

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

            self.execute_query(query, {"rels": rel_data})
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
        }

        stats = {}
        for key, query in queries.items():
            result = self.execute_query(query)
            stats[key] = result[0]["count"] if result else 0

        return stats

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
        OPTIONAL MATCH (f)-[:CONTAINS]->(entity)
        DETACH DELETE f, entity
        RETURN count(f) + count(entity) as deletedCount
        """
        result = self.execute_query(query, {"path": file_path})
        deleted_count = result[0]["deletedCount"] if result else 0
        logger.info(f"Deleted {deleted_count} nodes for file: {file_path}")
        return deleted_count

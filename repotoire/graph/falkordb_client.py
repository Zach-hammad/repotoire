"""FalkorDB database client.

Drop-in replacement for Neo4jClient using FalkorDB (Redis-based graph database).

Stability fixes (REPO-500):
- Default socket timeouts to prevent indefinite hangs
- Increased retries with jitter for Redis loading states
- Error categorization (transient vs permanent)
- Connection health checks and automatic reconnection
- Circuit breaker pattern for cascading failure prevention
"""

from typing import Any, Dict, List, Optional, Set
import logging
import random
import time

from repotoire.graph.base import DatabaseClient
from repotoire.models import Entity, Relationship, NodeType, RelationshipType
from repotoire.validation import validate_identifier

logger = logging.getLogger(__name__)

# Transient error patterns that should be retried
TRANSIENT_ERROR_PATTERNS: Set[str] = {
    "LOADING",  # Redis is loading the dataset
    "BUSY",  # Redis is busy
    "CLUSTERDOWN",  # Cluster is down
    "TRYAGAIN",  # Retry later
    "MOVED",  # Cluster slot moved
    "ASK",  # Cluster redirect
    "connection",  # Connection errors
    "timeout",  # Timeout errors
    "reset",  # Connection reset
    "refused",  # Connection refused
    "Broken pipe",  # Broken pipe
    "No route to host",  # Network unreachable
    "Name or service not known",  # DNS resolution
    "No address associated",  # DNS resolution
}

# Permanent error patterns that should NOT be retried
PERMANENT_ERROR_PATTERNS: Set[str] = {
    "NOAUTH",  # Authentication required
    "WRONGPASS",  # Wrong password
    "NOPERM",  # No permission
    "syntax error",  # Query syntax error
    "Invalid",  # Invalid query/parameters
    "unknown command",  # Unknown command
}


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
        socket_timeout: Optional[float] = 30.0,  # REPO-500: Default 30s to prevent hangs
        socket_connect_timeout: Optional[float] = 10.0,  # REPO-500: Default 10s connect timeout
        max_connections: Optional[int] = None,
        max_retries: int = 5,  # REPO-500: Increased from 3 for Redis loading states
        retry_base_delay: float = 2.0,  # REPO-500: Increased from 1.0
        retry_backoff_factor: float = 2.0,
        retry_jitter: float = 0.5,  # REPO-500: Jitter to prevent thundering herd
        retry_max_delay: float = 60.0,  # REPO-500: Cap maximum delay
        default_query_timeout: float = 30.0,  # Default timeout for queries in seconds
        circuit_breaker_threshold: int = 5,  # REPO-500: Failures before opening circuit
        circuit_breaker_timeout: float = 30.0,  # REPO-500: Time before half-open
        **kwargs,  # Accept but ignore Neo4j-specific params
    ):
        """Initialize FalkorDB client.

        Args:
            host: FalkorDB host
            port: FalkorDB port (Redis protocol)
            graph_name: Name of the graph to use
            password: Optional Redis password
            ssl: Enable TLS/SSL connection (required for Fly.io external access)
            socket_timeout: Socket timeout in seconds (default 30s)
            socket_connect_timeout: Connection timeout in seconds (default 10s)
            max_connections: Maximum connection pool size (default: 100)
            max_retries: Maximum retry attempts (default 5 for Redis loading states)
            retry_base_delay: Base delay between retries (default 2s)
            retry_backoff_factor: Backoff multiplier (default 2.0)
            retry_jitter: Random jitter factor 0-1 to prevent thundering herd (default 0.5)
            retry_max_delay: Maximum delay cap in seconds (default 60s)
            default_query_timeout: Default timeout for queries in seconds (default 30s)
            circuit_breaker_threshold: Consecutive failures before opening circuit (default 5)
            circuit_breaker_timeout: Seconds before trying again after circuit opens (default 30s)
        """
        self.host = host
        self.port = port
        self.graph_name = graph_name
        self.password = password
        self.ssl = ssl
        self.socket_timeout = socket_timeout
        self.socket_connect_timeout = socket_connect_timeout
        self.max_connections = max_connections or 100  # Default pool size (production-ready)
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_backoff_factor = retry_backoff_factor
        self.retry_jitter = retry_jitter
        self.retry_max_delay = retry_max_delay
        self.default_query_timeout = default_query_timeout

        # REPO-500: Circuit breaker state
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.circuit_breaker_timeout = circuit_breaker_timeout
        self._circuit_failures = 0
        self._circuit_open_time: Optional[float] = None
        self._last_health_check: float = 0.0
        self._health_check_interval: float = 60.0  # Check health every 60s

        # Connect to FalkorDB
        self._connect()
        logger.info(f"Connected to FalkorDB at {host}:{port}, graph: {graph_name}, pool_size: {self.max_connections}")

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
        # REPO-500: Always set socket timeouts to prevent indefinite hangs
        if self.socket_timeout is not None:
            conn_kwargs["socket_timeout"] = self.socket_timeout
        if self.socket_connect_timeout is not None:
            conn_kwargs["socket_connect_timeout"] = self.socket_connect_timeout
        # Configure connection pool size to prevent unbounded connection growth
        if self.max_connections is not None:
            conn_kwargs["max_connections"] = self.max_connections

        self.db = FalkorDB(**conn_kwargs)
        self.graph = self.db.select_graph(self.graph_name)

        # REPO-500: Reset circuit breaker on successful connection
        self._circuit_failures = 0
        self._circuit_open_time = None

    def _reconnect(self) -> bool:
        """Attempt to reconnect to FalkorDB.

        REPO-500: Called when connection appears stale or after failures.

        Returns:
            True if reconnection successful, False otherwise
        """
        logger.info(f"Attempting to reconnect to FalkorDB at {self.host}:{self.port}")
        try:
            self._connect()
            logger.info("Reconnection successful")
            return True
        except Exception as e:
            logger.warning(f"Reconnection failed: {e}")
            return False

    def _check_health(self) -> bool:
        """Check connection health with PING.

        REPO-500: Periodic health check to detect stale connections.

        Returns:
            True if healthy, False otherwise
        """
        try:
            # Access the underlying Redis connection for PING
            if hasattr(self.db, '_client') and self.db._client:
                self.db._client.ping()
            elif hasattr(self.db, 'connection') and self.db.connection:
                self.db.connection.ping()
            else:
                # Fallback: execute a simple query
                self.graph.query("RETURN 1", timeout=5000)
            return True
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False

    def _is_circuit_open(self) -> bool:
        """Check if circuit breaker is open.

        REPO-500: Circuit breaker pattern to prevent cascading failures.

        Returns:
            True if circuit is open (should fail fast), False otherwise
        """
        if self._circuit_open_time is None:
            return False

        # Check if we should try again (half-open state)
        elapsed = time.time() - self._circuit_open_time
        if elapsed >= self.circuit_breaker_timeout:
            logger.info("Circuit breaker entering half-open state, allowing test request")
            return False

        return True

    def _record_success(self) -> None:
        """Record successful operation for circuit breaker."""
        self._circuit_failures = 0
        self._circuit_open_time = None

    def _record_failure(self) -> None:
        """Record failed operation for circuit breaker."""
        self._circuit_failures += 1
        if self._circuit_failures >= self.circuit_breaker_threshold:
            if self._circuit_open_time is None:
                logger.warning(
                    f"Circuit breaker OPEN after {self._circuit_failures} consecutive failures. "
                    f"Will retry in {self.circuit_breaker_timeout}s"
                )
                self._circuit_open_time = time.time()

    def _is_transient_error(self, error: Exception) -> bool:
        """Check if error is transient and should be retried.

        REPO-500: Categorize errors to avoid wasting time on permanent failures.

        Args:
            error: The exception to check

        Returns:
            True if transient (retry), False if permanent (fail fast)
        """
        error_str = str(error).upper()

        # Check for permanent errors first (fail fast)
        for pattern in PERMANENT_ERROR_PATTERNS:
            if pattern.upper() in error_str:
                logger.debug(f"Permanent error detected (no retry): {pattern}")
                return False

        # Check for known transient errors
        for pattern in TRANSIENT_ERROR_PATTERNS:
            if pattern.upper() in error_str:
                logger.debug(f"Transient error detected (will retry): {pattern}")
                return True

        # Default: treat as transient for safety
        return True

    def _calculate_retry_delay(self, attempt: int) -> float:
        """Calculate retry delay with jitter.

        REPO-500: Exponential backoff with jitter to prevent thundering herd.

        Args:
            attempt: Current attempt number (1-based)

        Returns:
            Delay in seconds
        """
        # Exponential backoff
        delay = self.retry_base_delay * (self.retry_backoff_factor ** (attempt - 1))

        # Add jitter (random factor)
        jitter = delay * self.retry_jitter * random.random()
        delay = delay + jitter

        # Cap at maximum
        delay = min(delay, self.retry_max_delay)

        return delay

    def close(self) -> None:
        """Close database connection."""
        # REPO-500: Proper cleanup of connection pool
        try:
            if hasattr(self.db, '_client') and self.db._client:
                self.db._client.close()
            elif hasattr(self.db, 'connection') and self.db.connection:
                self.db.connection.close()
        except Exception as e:
            logger.debug(f"Error during connection close: {e}")
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

        REPO-500: Enhanced with circuit breaker, error categorization, and reconnection.

        Args:
            query: Cypher query string
            parameters: Query parameters
            timeout: Query timeout in seconds (uses default_query_timeout if not specified)

        Returns:
            List of result records as dictionaries

        Raises:
            Exception: If query fails after all retries or circuit is open
        """
        params = parameters or {}
        query_timeout = timeout if timeout is not None else self.default_query_timeout

        # REPO-500: Check circuit breaker
        if self._is_circuit_open():
            raise ConnectionError(
                f"Circuit breaker is OPEN. FalkorDB at {self.host}:{self.port} has "
                f"{self._circuit_failures} consecutive failures. "
                f"Will retry in {self.circuit_breaker_timeout - (time.time() - (self._circuit_open_time or 0)):.1f}s"
            )

        # REPO-500: Periodic health check
        now = time.time()
        if now - self._last_health_check > self._health_check_interval:
            self._last_health_check = now
            if not self._check_health():
                logger.warning("Health check failed, attempting reconnection")
                self._reconnect()

        attempt = 0
        last_exception: Optional[Exception] = None

        while attempt <= self.max_retries:
            try:
                # FalkorDB supports timeout parameter in query()
                result = self.graph.query(query, params, timeout=int(query_timeout * 1000))

                # REPO-500: Record success for circuit breaker
                self._record_success()

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

                # REPO-500: Check if error is permanent (don't retry)
                if not self._is_transient_error(e):
                    logger.error(f"Permanent error (not retrying): {e}")
                    logger.error(f"Failed query: {query}")
                    self._record_failure()
                    raise

                # REPO-500: Record failure for circuit breaker
                self._record_failure()

                if attempt > self.max_retries:
                    logger.error(f"Query failed after {self.max_retries} retries: {e}")
                    logger.error(f"Failed query: {query}")
                    # Don't log params in production - may contain sensitive data
                    logger.debug(f"Failed params: {params}")
                    raise

                # REPO-500: Calculate delay with jitter
                delay = self._calculate_retry_delay(attempt)
                logger.warning(
                    f"Query attempt {attempt}/{self.max_retries} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )

                # REPO-500: Try reconnection on connection errors
                error_str = str(e).lower()
                if "connection" in error_str or "reset" in error_str or "refused" in error_str:
                    logger.info("Connection error detected, attempting reconnection before retry")
                    self._reconnect()

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

        # Validate node type is a known enum value to prevent query injection
        validated_node_type = validate_identifier(entity.node_type.value)

        query = f"""
        CREATE (n:{validated_node_type} {{
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

        # Validate labels and relationship types to prevent query injection
        validated_external_label = validate_identifier(external_label)
        validated_rel_type = validate_identifier(rel.rel_type.value)

        # KG-1 Fix: Create external nodes with proper label
        # KG-2 Fix: Use MERGE for relationships
        query = f"""
        MATCH (source {{qualifiedName: $source_id}})
        MERGE (target:{validated_external_label} {{qualifiedName: $target_qualified_name}})
        ON CREATE SET target.name = $target_name, target.external = true
        MERGE (source)-[r:{validated_rel_type}]->(target)
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
            # REPO-500: Use n = node instead of n += node to fully replace properties
            # This prevents stale hash/lastModified from persisting after updates
            if node_type == "File":
                query = f"""
                UNWIND $nodes AS node
                MERGE (n:{validated_node_type} {{filePath: node.filePath}})
                ON CREATE SET n = node
                ON MATCH SET n = node
                RETURN n.qualifiedName as qualifiedName
                """
            else:
                query = f"""
                UNWIND $nodes AS node
                MERGE (n:{validated_node_type} {{qualifiedName: node.qualifiedName}})
                ON CREATE SET n = node
                ON MATCH SET n = node
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
                        # REPO-500: Use n = $props instead of n += $props to fully replace
                        if node_type == "File":
                            fallback_query = f"""
                            MERGE (n:{validated_node_type} {{filePath: $filePath}})
                            ON CREATE SET n = $props
                            ON MATCH SET n = $props
                            RETURN n.qualifiedName as qualifiedName
                            """
                            params = {"filePath": e_data["filePath"], "props": e_data}
                        else:
                            fallback_query = f"""
                            MERGE (n:{validated_node_type} {{qualifiedName: $qualifiedName}})
                            ON CREATE SET n = $props
                            ON MATCH SET n = $props
                            RETURN n.qualifiedName as qualifiedName
                            """
                            params = {"qualifiedName": e_data["qualifiedName"], "props": e_data}
                        result = self.execute_query(fallback_query, params)
                        if result:
                            id_mapping[e_data["qualifiedName"]] = e_data["qualifiedName"]
                    except Exception as inner_e:
                        logger.warning(f"Failed to create node {e_data.get('qualifiedName')}: {inner_e}")

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

        return total_created

    def clear_graph(self) -> None:
        """Delete all nodes and relationships."""
        # FalkorDB: delete the graph and recreate
        try:
            self.graph.delete()
        except Exception as e:
            # Graph might not exist yet, which is fine
            logger.debug(f"Could not delete graph (may not exist): {e}")
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
            except Exception as e:
                logger.debug(f"Could not get stat '{key}': {e}")
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
        """Get sample nodes of a specific label.

        Args:
            label: Node label to sample
            limit: Maximum number of nodes to return (default 5, max 100)

        Returns:
            List of node property dictionaries
        """
        validated_label = validate_identifier(label, "label")
        # Validate and clamp limit to prevent abuse (max 100)
        safe_limit = max(1, min(int(limit), 100))
        query = f"""
        MATCH (n:{validated_label})
        RETURN n
        LIMIT $limit
        """
        result = self.execute_query(query, parameters={"limit": safe_limit})
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
        except Exception as e:
            logger.debug(f"Could not validate schema integrity: {e}")
            issues["validation_error"] = str(e)

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

    def batch_get_file_metadata(self, file_paths: List[str]) -> Dict[str, Dict[str, Any]]:
        """Get file metadata for multiple files in a single query.

        Performance: Uses UNWIND to fetch all metadata in O(1) query instead of O(N).

        Args:
            file_paths: List of file paths to fetch metadata for

        Returns:
            Dict mapping file_path to metadata dict (hash, lastModified).
            Files not found in database are not included in result.
        """
        if not file_paths:
            return {}

        query = """
        UNWIND $paths AS path
        MATCH (f:File {filePath: path})
        RETURN f.filePath as filePath, f.hash as hash, f.lastModified as lastModified
        """
        result = self.execute_query(query, {"paths": file_paths})

        return {
            record["filePath"]: {
                "hash": record["hash"],
                "lastModified": record["lastModified"]
            }
            for record in result
            if record.get("filePath")
        }

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

    def batch_delete_file_entities(self, file_paths: List[str]) -> int:
        """Delete multiple files and their related entities in a single query.

        Performance: Uses UNWIND to delete all files in O(1) query instead of O(N).

        Args:
            file_paths: List of file paths to delete

        Returns:
            Total count of deleted nodes
        """
        if not file_paths:
            return 0

        query = """
        UNWIND $paths AS path
        MATCH (f:File {filePath: path})
        OPTIONAL MATCH (f)-[:CONTAINS*]->(entity)
        WITH f, collect(entity) as entities
        DETACH DELETE f
        WITH entities
        UNWIND entities as entity
        DETACH DELETE entity
        RETURN count(*) as deletedCount
        """
        try:
            result = self.execute_query(query, {"paths": file_paths})
            deleted_count = result[0]["deletedCount"] if result else 0
            logger.info(f"Batch deleted {deleted_count} nodes for {len(file_paths)} files")
            return deleted_count
        except Exception as e:
            # Fallback to individual deletes if batch fails
            logger.warning(f"Batch delete failed, falling back to individual deletes: {e}")
            total_deleted = 0
            for path in file_paths:
                total_deleted += self.delete_file_entities(path)
            return total_deleted

    def batch_update_embeddings(
        self,
        updates: List[Dict[str, Any]],
        entity_type: str = "Function",
        chunk_size: int = 500,
    ) -> int:
        """Batch update embeddings using UNWIND for O(1) query instead of O(N).

        Performance: Reduces N network round-trips to 1, providing 50-100x speedup
        for large batches.

        Args:
            updates: List of dicts with keys:
                - id: Entity ID (qualified_name or element_id depending on id_func)
                - embedding: List of floats (the embedding vector)
                - dims: int (embedding dimensions)
                - compressed: bool (whether PCA compressed)
            entity_type: Node label (Function, Class, File)
            chunk_size: Max updates per query (default 500)

        Returns:
            Total count of updated nodes
        """
        if not updates:
            return 0

        total_updated = 0

        # Process in chunks to avoid memory issues
        for i in range(0, len(updates), chunk_size):
            chunk = updates[i : i + chunk_size]

            # Use qualifiedName for matching (indexed)
            query = f"""
            UNWIND $updates AS update
            MATCH (e:{entity_type} {{qualifiedName: update.id}})
            SET e.embedding = vecf32(update.embedding),
                e.embedding_dims = update.dims,
                e.embedding_compressed = update.compressed
            RETURN count(e) AS updated
            """

            try:
                # Prepare data for query
                query_updates = [
                    {
                        "id": u["id"],
                        "embedding": u["embedding"],
                        "dims": u.get("dims", len(u["embedding"])),
                        "compressed": u.get("compressed", False),
                    }
                    for u in chunk
                ]

                result = self.execute_query(query, {"updates": query_updates})
                updated = result[0]["updated"] if result else 0
                total_updated += updated

            except Exception as e:
                # Fallback to individual updates if batch fails
                logger.warning(
                    f"Batch embedding update failed for chunk {i // chunk_size}, "
                    f"falling back to individual updates: {e}"
                )
                for update in chunk:
                    try:
                        individual_query = f"""
                        MATCH (e:{entity_type} {{qualifiedName: $id}})
                        SET e.embedding = vecf32($embedding),
                            e.embedding_dims = $dims,
                            e.embedding_compressed = $compressed
                        RETURN count(e) AS updated
                        """
                        result = self.execute_query(
                            individual_query,
                            {
                                "id": update["id"],
                                "embedding": update["embedding"],
                                "dims": update.get("dims", len(update["embedding"])),
                                "compressed": update.get("compressed", False),
                            },
                        )
                        if result and result[0]["updated"] > 0:
                            total_updated += 1
                    except Exception as inner_e:
                        logger.error(
                            f"Failed to update embedding for {update.get('id')}: {inner_e}"
                        )

        logger.info(
            f"Batch updated {total_updated} embeddings for {entity_type} "
            f"({len(updates)} requested)"
        )
        return total_updated

    def get_pool_metrics(self) -> Dict[str, Any]:
        """Get connection metrics."""
        return {
            "host": self.host,
            "port": self.port,
            "graph_name": self.graph_name,
            "backend": "FalkorDB",
        }
